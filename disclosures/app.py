"""HTTP service.

POST /ask      runs the multi-agent assistant
GET  /search   retrieval only, in one corpus (no model call)
GET  /health   which indexes are loaded and how fresh they are
GET  /         a one-page web client
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agents import Assistant
from .config import Settings
from .index import CORPORA
from .retrieve import Filters, Retriever
from .telemetry import cost_usd, log
from .wiring import build_assistant, load_retrievers

STATIC = Path(__file__).parent / "static"
DATE = r"^\d{4}-\d{2}-\d{2}$"


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    as_of: str | None = Field(default=None, pattern=DATE, description="Answer as of this date")


class Limiter:
    """Spending guard for a public endpoint: a per-client rate limit and a daily total.

    Counters live in memory, so they hold per instance. The service runs with at most one
    instance (see infra/), which makes the daily total a real ceiling.
    """

    def __init__(self, per_minute: int, per_day: int) -> None:
        self.per_minute = per_minute
        self.per_day = per_day
        self.recent: dict[str, deque[float]] = defaultdict(deque)
        self.day = ""
        self.count = 0

    def check(self, client: str) -> None:
        now = time.time()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.count = today, 0
            self.recent.clear()
        if self.count >= self.per_day:
            raise HTTPException(429, "The daily question limit for this demo has been reached.")
        window = self.recent[client]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            raise HTTPException(429, "Too many questions, try again in a minute.")
        window.append(now)
        self.count += 1


def create_app(
    assistant: Assistant | None = None,
    retrievers: dict[str, Retriever] | None = None,
    cfg: Settings | None = None,
) -> FastAPI:
    """The web service. Tests pass a ready assistant and retrievers. Production loads them."""
    cfg = cfg or Settings()
    limiter = Limiter(cfg.per_ip_per_min, cfg.daily_cap)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.retrievers = load_retrievers(cfg) if retrievers is None else retrievers
        app.state.assistant = assistant
        yield

    app = FastAPI(title="Swedish Disclosure RAG", lifespan=lifespan)

    def get_assistant() -> Assistant:
        # Built on the first question, so search and health checks work without model credentials.
        if app.state.assistant is None:
            app.state.assistant = build_assistant(cfg, app.state.retrievers)
        return app.state.assistant

    @app.get("/health")
    def health() -> dict:
        corpora = {
            name: {"passages": len(r.index.chunks), "newest": r.newest()}
            for name, r in app.state.retrievers.items()
        }
        return {"status": "ok", "corpora": corpora}

    @app.get("/search")
    def search(
        q: str,
        corpus: str = "press",
        as_of: str | None = None,
        company: str | None = None,
        k: int = 8,
    ) -> dict:
        if corpus not in app.state.retrievers:
            raise HTTPException(404, f"No index for {corpus!r}. Choose from {list(CORPORA)}.")
        hits = app.state.retrievers[corpus].search(
            q, k=min(k, 20), filters=Filters(as_of=as_of, company=company)
        )
        return {"hits": [asdict(h.chunk) | {"score": round(h.score, 4)} for h in hits]}

    @app.post("/ask")
    def ask(body: AskRequest, request: Request) -> dict:
        # Cloud Run puts the caller's address first in X-Forwarded-For.
        forwarded = request.headers.get("x-forwarded-for", "")
        client = forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")
        limiter.check(client)

        try:
            ans = get_assistant().answer(body.question, as_of=body.as_of)
        except Exception as err:  # model or network failure: log it, return a clean error
            log("ask_failed", severity="ERROR", error=repr(err)[:500])
            raise HTTPException(502, "The language model could not be reached.") from err

        cost = cost_usd(ans.tokens_in, ans.tokens_out, cfg.price_in, cfg.price_out)
        log(
            "ask",
            latency_ms=ans.latency_ms,
            tokens_in=ans.tokens_in,
            tokens_out=ans.tokens_out,
            cost_usd=cost,
            answerable=ans.answerable,
            citations=len(ans.citations),
            specialists=[f.specialist for f in ans.findings],
            tool_calls=sum(len(f.tool_calls) for f in ans.findings),
            stopped_early=any(f.stopped_early for f in ans.findings),
            as_of=body.as_of,
            model=cfg.llm_model,
        )
        return {
            "answer": ans.text,
            "answerable": ans.answerable,
            "citations": [asdict(s) for s in ans.citations],
            "specialists": [
                {
                    "name": f.specialist,
                    "question": f.question,
                    "answerable": f.answerable,
                    "tool_calls": f.tool_calls,
                }
                for f in ans.findings
            ],
            "latency_ms": ans.latency_ms,
            "tokens": {"in": ans.tokens_in, "out": ans.tokens_out},
            "cost_usd": cost,
        }

    @app.get("/")
    def home() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app
