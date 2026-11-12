"""Shared fakes and fixtures: a deterministic embedder, a scripted chat model and a small
corpus for each of the three sources, so the tests need no network and no model."""

import hashlib

import numpy as np
import pytest
from langchain_core.messages import AIMessage

from disclosures import store
from disclosures.agents.supervisor import Assignment, Plan
from disclosures.cloud.warehouse import SqliteWarehouse
from disclosures.index import build, chunk_release, chunk_report, tokenize
from disclosures.retrieve import Retriever
from disclosures.store import PAGE_BREAK, TRADE_COLUMNS, Release, Report


class HashEmbedder:
    """Bag-of-words vectors from hashed tokens: texts sharing words get similar vectors."""

    def embed(self, texts, query=False):
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for i, text in enumerate(texts):
            for tok in tokenize(text):
                out[i, int(hashlib.md5(tok.encode()).hexdigest(), 16) % 64] += 1
        return out / np.linalg.norm(out, axis=1, keepdims=True).clip(min=1e-9)


class ScriptedModel:
    """Stands in for a chat model: replays fixed replies in order and records what it was sent.

    A reply is an AIMessage, or a Plan when the caller asked for structured output.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []  # one list of messages per call

    def bind_tools(self, tools):
        return self

    def with_structured_output(self, schema, include_raw=False):
        return _Structured(self)

    def invoke(self, messages):
        self.sent.append(list(messages))
        return self.replies.pop(0)


class _Structured:
    def __init__(self, model):
        self.model = model

    def invoke(self, messages):
        return {
            "raw": AIMessage("", usage_metadata=_usage(50, 5)),
            "parsed": self.model.invoke(messages),
        }


def _usage(tokens_in, tokens_out):
    return {
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "total_tokens": tokens_in + tokens_out,
    }


def tool_call(name, **args):
    """A model reply that calls one tool."""
    call = {"name": name, "args": args, "id": "call-1"}
    return AIMessage("", tool_calls=[call], usage_metadata=_usage(100, 10))


def search(**args):
    return tool_call("search", **args)


def final(text):
    """A model reply that ends the conversation with `text`."""
    return AIMessage(text, usage_metadata=_usage(300, 40))


def plan(**questions):
    """A supervisor reply: plan(press="...", insider="...")."""
    return Plan(assignments=[Assignment(specialist=k, question=v) for k, v in questions.items()])


def tool_result(model, call=1):
    """What the specialist sent back to the model after its `call`-th tool call, as a dict."""
    import json

    return json.loads(model.sent[call][-1].content)


def release(id, company, headline, published, text, category="Nyheter för investerare"):
    return Release(
        id, company, headline, category, "sv", published, f"https://example.test/{id}", text
    )


RELEASES = [
    release(
        1,
        "Alfa AB",
        "Alfa byter vd",
        "2026-03-01T07:00:00Z",
        "Styrelsen i Alfa har utsett Anna Berg till ny verkställande direktör.",
        "Ändring av styrelse/ledning/revisor",
    ),
    release(
        2,
        "Beta AB",
        "Beta förvärvar Gamma",
        "2026-06-15T06:30:00Z",
        "Beta har tecknat avtal om att förvärva samtliga aktier i Gamma för 400 MSEK.",
    ),
    release(
        3,
        "Alfa AB",
        "Alfa sänker prognosen",
        "2026-09-10T16:00:00Z",
        "Alfa sänker helårsprognosen för rörelseresultatet till 120 MSEK från 180 MSEK.",
        "Insiderinformation",
    ),
]

REPORTS = [
    Report(
        id="a1",
        release_id=10,
        company="Alfa AB",
        headline="Alfa delårsrapport januari-juni 2026",
        category="Halvårsrapport",
        published="2026-07-20T06:00:00Z",
        url="https://example.test/a1.pdf",
        file_name="alfa-q2.pdf",
        text=PAGE_BREAK.join(
            [
                "Alfa delårsrapport. Nettoomsättningen uppgick till 900 MSEK.",
                "Segment Norden: rörelseresultatet uppgick till 75 MSEK under kvartalet.",
            ]
        ),
    ),
]


def trade(published, issuer, person, nature, volume, price, **other):
    row = dict.fromkeys((c.name for c in TRADE_COLUMNS), "")
    flags = {c.name: 0 for c in TRADE_COLUMNS if c.kind == "flag"}
    return (
        row
        | flags
        | {
            "published": published,
            "issuer": issuer,
            "person": person,
            "notifier": person,
            "nature": nature,
            "volume": volume,
            "price": price,
            "volume_unit": "Antal",
            "currency": "SEK",
            "status": "Aktuell",
            "transaction_date": published[:10],
        }
        | other
    )


TRADES = [
    trade("2026-03-05T09:00:00Z", "Alfa AB", "Anna Berg", "Förvärv", 1000.0, 50.0),
    trade("2026-09-15T09:00:00Z", "Alfa AB", "Anna Berg", "Förvärv", 2000.0, 40.0),
    trade("2026-09-16T09:00:00Z", "Beta AB", "Bo Ek", "Avyttring", 500.0, 100.0),
]


@pytest.fixture
def retriever():
    """Retriever over the press releases."""
    embedder = HashEmbedder()
    return Retriever(build([c for r in RELEASES for c in chunk_release(r)], embedder), embedder)


@pytest.fixture
def reports_retriever():
    embedder = HashEmbedder()
    return Retriever(build([c for r in REPORTS for c in chunk_report(r)], embedder), embedder)


@pytest.fixture
def warehouse(tmp_path):
    """The SQLite warehouse over a store holding TRADES."""
    con = store.connect(tmp_path / "store.db")
    store.replace_trades(con, "2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z", TRADES)
    con.close()
    return SqliteWarehouse(tmp_path / "store.db")
