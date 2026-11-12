"""End-to-end evaluation of the assistant. Calls the language model, so it costs tokens.

Reported:

    answered            share of answerable questions that got an answer (not a refusal)
    refused             share of unanswerable questions that were correctly refused
    routed              share of questions where every expected specialist was consulted
    cited_gold          share of questions with a gold release whose answer cites one
    citation_precision  share of cited documents, on those questions, that are gold
    contains            share of questions with expected strings whose answer has them all
    leaks               cited documents published after the question's date, plus answers
                        that state a fact from after that date (must be 0)

Whether an answer's text is right is otherwise not scored automatically. The answers are
written to results/ for manual review.

`--specialists` chooses who the assistant may consult, which is how one specialist alone is
compared with the full team on the same questions:

    uv run python -m evals.answers --specialists press        # one specialist, no supervisor or judge
    uv run python -m evals.answers                            # all three specialists
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, replace
from pathlib import Path

from disclosures.agents import Answer
from disclosures.config import Settings
from disclosures.telemetry import cost_usd
from disclosures.wiring import build_assistant, load_retrievers

from .common import Question, load, mentions


def record(q: Question, a: Answer, cfg: Settings) -> dict:
    """One question and what the assistant did with it, as a line of the results file."""
    documents = [s for s in a.citations if s.release_id is not None]
    return {
        **asdict(q),
        "answer": a.text,
        "answerable": a.answerable,
        "consulted": [f.specialist for f in a.findings],
        "cited": [s.release_id for s in documents],
        "cited_ids": [s.id for s in a.citations],
        "leaks": sum(s.published[:10] > q.as_of for s in documents)
        + sum(mentions(a.text, text) for text in q.forbidden),
        "has_expected": all(mentions(a.text, text) for text in q.contains),
        "tool_calls": [call for f in a.findings for call in f.tool_calls],
        "latency_ms": a.latency_ms,
        "cost_usd": cost_usd(a.tokens_in, a.tokens_out, cfg.price_in, cfg.price_out),
    }


def summarise(records: list[dict]) -> dict:
    def share(part: list, whole: list) -> float | None:
        return round(len(part) / len(whole), 3) if whole else None

    can = [r for r in records if r["gold"] or r["contains"]]
    cannot = [r for r in records if not (r["gold"] or r["contains"])]
    with_gold = [r for r in records if r["gold"]]
    with_text = [r for r in records if r["contains"]]
    cited = [(c, r["gold"]) for r in with_gold for c in r["cited"]]
    return {
        "questions": len(records),
        "answered": share([r for r in can if r["answerable"]], can),
        "refused": share([r for r in cannot if not r["answerable"]], cannot),
        "routed": share([r for r in can if set(r["specialists"]) <= set(r["consulted"])], can),
        "cited_gold": share([r for r in with_gold if set(r["cited"]) & set(r["gold"])], with_gold),
        "citation_precision": share([c for c, gold in cited if c in gold], cited),
        "contains": share(
            [r for r in with_text if r["answerable"] and r["has_expected"]], with_text
        ),
        "leaks": sum(r["leaks"] for r in records),
        "mean_latency_ms": round(sum(r["latency_ms"] for r in records) / len(records)),
        "mean_tool_calls": round(sum(len(r["tool_calls"]) for r in records) / len(records), 2),
        "total_cost_usd": round(sum(r["cost_usd"] for r in records), 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--specialists", help="comma-separated, default is the SPECIALISTS setting")
    ap.add_argument("--only", nargs="+", help="ids of the questions to run")
    ap.add_argument(
        "--pause", type=float, default=4.0, help="seconds between questions (free-tier rate limits)"
    )
    ap.add_argument("--out", type=Path, default=Path("results"))
    args = ap.parse_args()

    cfg = Settings()
    if args.specialists:
        cfg = replace(cfg, specialists=args.specialists)
    assistant = build_assistant(cfg, load_retrievers(cfg))
    team = "+".join(assistant.specialists)

    records = []
    for q in load(args.split):
        if args.only and q.id not in args.only:
            continue
        r = record(q, assistant.answer(q.question, as_of=q.as_of), cfg)
        records.append(r)
        outcome = "answered" if r["answerable"] else "refused"
        print(f"{q.id}: {outcome} via {r['consulted']} citing {r['cited_ids']}", flush=True)
        time.sleep(args.pause)

    summary = {"split": args.split, "model": cfg.llm_model, "team": team, **summarise(records)}
    args.out.mkdir(parents=True, exist_ok=True)
    name = f"answers-{args.split}-{team}"
    with (args.out / f"{name}.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (args.out / f"{name}.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
