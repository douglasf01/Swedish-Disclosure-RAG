"""Search indexes: documents are split into passages, embedded, and stored as plain files.

There is one index per corpus, each in its own directory:

    data/index/press/      passages of press releases
    data/index/reports/    passages of report PDFs, each with its page number

    chunks.jsonl     one passage per line, with the document's metadata
    embeddings.npy   float32 matrix, row i belongs to line i, rows are unit length

Rebuilding reuses the vectors of passages that are already indexed, so the daily job only
embeds what is new.

    uv run python -m disclosures.index [--only press] [--fresh]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from . import store
from .config import Settings
from .store import PAGE_BREAK, Release, Report

CORPORA = ("press", "reports")
FILES = ("chunks.jsonl", "embeddings.npy")  # what one index directory holds
CHUNK_CHARS = 700  # the embedding model was trained on short passages
EMBED_BATCH = 1000  # passages embedded between two progress lines
MAX_CHUNKS = 30  # per press release (the rare very long one is cut off)


@dataclass(frozen=True)
class Chunk:
    id: str  # unique within its corpus
    release_id: int  # the press release the passage belongs to, or was attached to
    company: str
    headline: str
    category: str
    published: str  # ISO 8601, UTC
    url: str
    text: str
    event: str | None = None
    page: int | None = None  # page of the PDF, for reports

    @property
    def embed_text(self) -> str:
        return f"{self.company}: {self.headline}\n{self.text}"


def index_files(corpora: tuple[str, ...] = CORPORA) -> list[str]:
    """The index files of the given corpora, relative to the data directory."""
    return [f"index/{corpus}/{name}" for corpus in corpora for name in FILES]


class Embedder(Protocol):
    def embed(self, texts: list[str], query: bool = False) -> np.ndarray: ...


E5_SMALL = "intfloat/multilingual-e5-small"


class FastEmbedder:
    """Multilingual E5 embeddings on CPU through ONNX (no PyTorch needed).

    E5 models are trained for retrieval with a "query: " or "passage: " prefix on the
    input, so questions and passages are embedded differently.
    """

    def __init__(self, model: str, cache_dir: str) -> None:
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType

        supported = {m["model"] for m in TextEmbedding.list_supported_models()}
        if model == E5_SMALL and model not in supported:
            TextEmbedding.add_custom_model(
                model=model,
                pooling=PoolingType.MEAN,
                normalization=True,
                sources=ModelSource(hf=model),
                dim=384,
                model_file="onnx/model.onnx",
            )
        self.e5 = "e5" in model.lower()
        self.model = TextEmbedding(model, cache_dir=cache_dir)

    def embed(self, texts: list[str], query: bool = False) -> np.ndarray:
        if self.e5:
            prefix = "query: " if query else "passage: "
            texts = [prefix + t for t in texts]
        vecs = np.asarray(list(self.model.embed(texts, batch_size=32)), dtype=np.float32)
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True).clip(min=1e-9)


def split_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Group consecutive lines into passages of at most `limit` characters."""
    pieces: list[str] = []
    for line in text.splitlines():
        while len(line) > limit:
            cut = max(line.rfind(". ", 0, limit), line.rfind("; ", 0, limit))
            cut = cut + 1 if cut > limit // 2 else line.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            pieces.append(line[:cut].strip())
            line = line[cut:].strip()
        if line:
            pieces.append(line)
    chunks: list[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + len(piece) + 1 <= limit:
            chunks[-1] += "\n" + piece
        else:
            chunks.append(piece)
    return chunks


def chunk_release(rel: Release) -> list[Chunk]:
    return [
        Chunk(
            id=f"{rel.id}-{n}",
            release_id=rel.id,
            company=rel.company,
            headline=rel.headline,
            category=rel.category,
            published=rel.published,
            url=rel.url,
            text=text,
            event=rel.event,
        )
        for n, text in enumerate(split_text(rel.text or "")[:MAX_CHUNKS], start=1)
    ]


def chunk_report(rep: Report) -> list[Chunk]:
    """Passages never cross a page, so each one can be cited with its page number."""
    return [
        Chunk(
            id=f"{rep.id}-{page}-{n}",
            release_id=rep.release_id,
            company=rep.company,
            headline=rep.headline,
            category=rep.category,
            published=rep.published,
            url=rep.url,
            text=text,
            page=page,
        )
        for page, page_text in enumerate((rep.text or "").split(PAGE_BREAK), start=1)
        for n, text in enumerate(split_text(page_text), start=1)
    ]


def corpus_chunks(con: sqlite3.Connection, corpus: str) -> list[Chunk]:
    """Every passage of one corpus, from the documents in the store."""
    if corpus == "press":
        return [c for rel in store.releases_with_text(con) for c in chunk_release(rel)]
    if corpus == "reports":
        return [c for rep in store.reports_with_text(con) for c in chunk_report(rep)]
    raise ValueError(f"unknown corpus {corpus!r}")


@dataclass
class Index:
    chunks: list[Chunk]
    vectors: np.ndarray

    @classmethod
    def load(cls, index_dir: Path) -> Index:
        with (index_dir / "chunks.jsonl").open(encoding="utf-8") as f:
            chunks = [Chunk(**json.loads(line)) for line in f]
        vectors = np.load(index_dir / "embeddings.npy")
        if len(chunks) != len(vectors):
            raise ValueError(f"index is inconsistent: {len(chunks)} chunks, {len(vectors)} vectors")
        return cls(chunks, vectors)

    def save(self, index_dir: Path) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        with (index_dir / "chunks.jsonl").open("w", encoding="utf-8") as f:
            for c in self.chunks:
                f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
        np.save(index_dir / "embeddings.npy", self.vectors)


def build(chunks: list[Chunk], embedder: Embedder, previous: Index | None = None) -> Index:
    """Embed `chunks`, reusing the vector of every passage `previous` already holds unchanged."""
    known = {}
    if previous is not None:
        known = {c.id: (c.text, previous.vectors[i]) for i, c in enumerate(previous.chunks)}
    dim = previous.vectors.shape[1] if previous is not None and len(previous.vectors) else 0
    todo = [i for i, c in enumerate(chunks) if c.id not in known or known[c.id][0] != c.text]
    vectors = np.zeros((len(chunks), dim), dtype=np.float32)
    # In batches, so a long build reports progress.
    for start in range(0, len(todo), EMBED_BATCH):
        batch = todo[start : start + EMBED_BATCH]
        new = embedder.embed([chunks[i].embed_text for i in batch])
        if vectors.shape[1] != new.shape[1]:  # first index of this corpus: size known only now
            vectors = np.zeros((len(chunks), new.shape[1]), dtype=np.float32)
        vectors[batch] = new
        if len(todo) > EMBED_BATCH:
            print(f"  embedded {start + len(batch)}/{len(todo)}", flush=True)
    fresh = set(todo)
    for i, c in enumerate(chunks):
        if i not in fresh:
            vectors[i] = known[c.id][1]
    print(f"indexed {len(chunks)} passages, {len(todo)} newly embedded", flush=True)
    return Index(chunks, vectors)


def rebuild(
    con: sqlite3.Connection, corpus: str, index_dir: Path, embedder: Embedder, fresh: bool = False
) -> Index:
    """Bring the index of one corpus on disk up to date with the store."""
    exists = (index_dir / "chunks.jsonl").exists()
    previous = Index.load(index_dir) if exists and not fresh else None
    index = build(corpus_chunks(con, corpus), embedder, previous)
    index.save(index_dir)
    return index


_WORD = re.compile(r"[0-9a-zåäöéü]+", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) > 1]


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the search indexes from the SQLite store.")
    ap.add_argument("--only", nargs="+", choices=CORPORA, default=list(CORPORA))
    ap.add_argument("--fresh", action="store_true", help="re-embed everything")
    args = ap.parse_args()
    cfg = Settings()
    con = store.connect(cfg.db_path)
    embedder = FastEmbedder(cfg.embed_model, cfg.model_cache)
    for corpus in args.only:
        print(f"{corpus}: ", end="")
        rebuild(con, corpus, cfg.index_dir(corpus), embedder, args.fresh)


if __name__ == "__main__":
    main()
