"""Hybrid retrieval over one index, with a hard point-in-time filter.

`as_of` is applied before ranking: a passage published after that moment cannot be returned,
whatever the query says. Evaluation and the live service use the same code path, and the
press and reports corpora use the same class with different indexes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi

from .index import Chunk, Embedder, Index, tokenize

RRF_K = 60


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


@dataclass(frozen=True)
class Filters:
    as_of: str | None = None  # ISO 8601 UTC, only releases published at or before this
    since: str | None = None
    company: str | None = None  # case-insensitive substring of the company name
    category: str | None = None  # case-insensitive substring of the exchange category
    event: str | None = None


def _iso(value: str, end_of_day: bool = False) -> str:
    """Accept 'YYYY-MM-DD' as well as full timestamps. ISO strings then compare correctly."""
    if len(value) == 10:
        return value + ("T23:59:59Z" if end_of_day else "T00:00:00Z")
    return value


class Retriever:
    def __init__(self, index: Index, embedder: Embedder) -> None:
        self.index = index
        self.embedder = embedder
        self.bm25 = BM25Okapi([tokenize(c.embed_text) for c in index.chunks] or [["empty"]])
        self.published = np.array([c.published for c in index.chunks])
        self.company = np.array([c.company.lower() for c in index.chunks])
        self.category = np.array([c.category.lower() for c in index.chunks])
        self.event = np.array([c.event or "" for c in index.chunks])

    def mask(self, f: Filters) -> np.ndarray:
        keep = np.ones(len(self.index.chunks), dtype=bool)
        if f.as_of:
            keep &= self.published <= _iso(f.as_of, end_of_day=True)
        if f.since:
            keep &= self.published >= _iso(f.since)
        if f.company:
            keep &= np.char.find(self.company, f.company.lower()) >= 0
        if f.category:
            keep &= np.char.find(self.category, f.category.lower()) >= 0
        if f.event:
            keep &= self.event == f.event
        return keep

    def search(
        self,
        query: str,
        k: int = 8,
        filters: Filters | None = None,
        mode: str = "hybrid",
        per_release: int = 2,
    ) -> list[Hit]:
        """Top `k` passages, at most `per_release` from the same release."""
        keep = self.mask(filters or Filters())
        if not keep.any():
            return []
        rankings = []
        if mode in ("bm25", "hybrid"):
            scores = np.asarray(self.bm25.get_scores(tokenize(query)), dtype=np.float32)
            rankings.append(np.where(keep & (scores > 0), scores, -np.inf))
        if mode in ("dense", "hybrid"):
            scores = self.index.vectors @ self.embedder.embed([query], query=True)[0]
            rankings.append(np.where(keep, scores, -np.inf))
        if not rankings:
            raise ValueError(f"unknown mode {mode!r}")

        if len(rankings) == 1:
            fused = rankings[0]
        else:
            # Reciprocal rank fusion: combines the two rankings without tuning score scales.
            fused = np.zeros(len(keep), dtype=np.float32)
            for scores in rankings:
                order = np.argsort(-scores)
                valid = np.isfinite(scores[order])
                ranks = np.arange(1, len(order) + 1)
                fused[order[valid]] += 1.0 / (RRF_K + ranks[valid])
            fused = np.where(fused > 0, fused, -np.inf)

        hits: list[Hit] = []
        taken: dict[int, int] = {}
        for i in np.argsort(-fused):
            if not np.isfinite(fused[i]) or len(hits) == k:
                break
            chunk = self.index.chunks[i]
            if taken.get(chunk.release_id, 0) < per_release:
                taken[chunk.release_id] = taken.get(chunk.release_id, 0) + 1
                hits.append(Hit(chunk, float(fused[i])))
        return hits

    def newest(self) -> str | None:
        """Publication time of the newest passage in the index."""
        return max((c.published for c in self.index.chunks), default=None)

    def companies(self, prefix: str = "", as_of: str | None = None, limit: int = 30) -> list[str]:
        keep = self.mask(Filters(as_of=as_of, company=prefix or None))
        names = sorted({c.company for c, ok in zip(self.index.chunks, keep) if ok})
        return names[:limit]
