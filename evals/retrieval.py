"""Retrieval evaluation: does the right release come back, and does anything leak?

Runs on one corpus at a time, over the questions meant for that corpus's specialist. For
every question with a gold release the query is the question text itself, searched as of
the question's date. Reported per search mode:

    hit@k   share of questions with a gold release among the top k passages
    MRR     mean reciprocal rank of the first gold passage
    leaks   passages returned from after the question's date (must be 0)

    uv run python -m evals.retrieval [--corpus press] [--split dev] [--min-hit-rate 0.8]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from disclosures.config import Settings
from disclosures.index import CORPORA, FastEmbedder, Index
from disclosures.retrieve import Filters, Retriever

from .common import load

K = 8
MODES = ["bm25", "dense", "hybrid"]


def evaluate(retriever: Retriever, questions, mode: str) -> dict:
    """Ranking metrics over the questions with a gold release. Leak checks cover all of them."""
    hits = {1: 0, 3: 0, K: 0}
    rr = 0.0
    leaks = 0
    trapped = 0
    answerable = [q for q in questions if q.gold]
    started = time.monotonic()
    for q in questions:
        found = retriever.search(q.question, k=K, mode=mode, filters=Filters(as_of=q.as_of))
        ids = [h.chunk.release_id for h in found]
        leaks += sum(h.chunk.published[:10] > q.as_of for h in found)
        trapped += bool(set(ids) & set(q.trap))
        rank = next((i for i, rid in enumerate(ids, start=1) if rid in q.gold), None)
        if rank:
            rr += 1 / rank
            for k in hits:
                hits[k] += rank <= k
    n = len(answerable)
    return {
        "mode": mode,
        "questions": n,
        **{f"hit@{k}": round(v / n, 3) for k, v in hits.items()},
        "mrr": round(rr / n, 3),
        "leaks": leaks,
        "trap_hits": trapped,
        "ms_per_query": round((time.monotonic() - started) * 1000 / len(questions), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--corpus", default="press", choices=CORPORA)
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--min-hit-rate", type=float, help=f"fail unless hybrid hit@{K} reaches this")
    ap.add_argument("--out", type=Path, default=Path("results"))
    args = ap.parse_args()

    cfg = Settings()
    index = Index.load(cfg.index_dir(args.corpus))
    retriever = Retriever(index, FastEmbedder(cfg.embed_model, cfg.model_cache))
    known = {c.release_id for c in index.chunks}
    questions = [q for q in load(args.split) if args.corpus in q.specialists]
    missing = [q.id for q in questions if q.gold and not set(q.gold) & known]
    if missing:
        print(
            f"skipping {len(missing)} questions whose gold release is not in this index: {missing}"
        )
        questions = [q for q in questions if q.id not in missing]
    if not any(q.gold for q in questions):
        sys.exit("no questions to evaluate")

    rows = [evaluate(retriever, questions, mode) for mode in MODES]
    cols = list(rows[0])
    print(" | ".join(cols))
    for row in rows:
        print(" | ".join(str(row[c]) for c in cols))
    args.out.mkdir(parents=True, exist_ok=True)
    summary = {
        "corpus": args.corpus,
        "split": args.split,
        "passages": len(index.chunks),
        "rows": rows,
    }
    (args.out / f"retrieval-{args.corpus}.json").write_text(json.dumps(summary, indent=2))

    hybrid = rows[-1]
    if hybrid["leaks"] or hybrid["trap_hits"]:
        sys.exit("point-in-time leak: a passage from after the question date was returned")
    if args.min_hit_rate is not None and hybrid[f"hit@{K}"] < args.min_hit_rate:
        sys.exit(f"hybrid hit@{K} {hybrid[f'hit@{K}']} is below the threshold {args.min_hit_rate}")


if __name__ == "__main__":
    main()
