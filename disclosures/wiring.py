"""Builds the running system from the settings: indexes, retrievers and the assistant.

The web service and the evaluations both start here, so they are guaranteed to run the
same thing.
"""

from __future__ import annotations

from .agents import Assistant, InsiderSpecialist, PressSpecialist, ReportsSpecialist, Specialist
from .cloud.llm import chat_model
from .cloud.storage import blob_store
from .cloud.warehouse import warehouse
from .config import Settings
from .index import CORPORA, FastEmbedder, Index, index_files
from .retrieve import Retriever
from .telemetry import log

DOCUMENT_SPECIALISTS = {"press": PressSpecialist, "reports": ReportsSpecialist}


def load_retrievers(cfg: Settings) -> dict[str, Retriever]:
    """One retriever per corpus that has an index, downloading the indexes first if needed."""
    if store := blob_store(cfg):
        store.download(index_files(CORPORA), cfg.data_dir)
    embedder = FastEmbedder(cfg.embed_model, cfg.model_cache)
    retrievers = {}
    for corpus in CORPORA:
        if (cfg.index_dir(corpus) / "chunks.jsonl").exists():
            retrievers[corpus] = Retriever(Index.load(cfg.index_dir(corpus)), embedder)
            log("index_loaded", corpus=corpus, passages=len(retrievers[corpus].index.chunks))
        else:
            log("index_missing", severity="WARNING", corpus=corpus)
    return retrievers


def build_assistant(cfg: Settings, retrievers: dict[str, Retriever]) -> Assistant:
    """The assistant with the specialists named in the settings.

    A document specialist whose index is missing is left out, so the service still starts
    when a corpus has not been built yet.
    """
    model = chat_model(cfg)
    specialists: list[Specialist] = []
    for name in (n.strip() for n in cfg.specialists.split(",")):
        if name in DOCUMENT_SPECIALISTS:
            if name in retrievers:
                specialists.append(DOCUMENT_SPECIALISTS[name](retrievers[name], model))
        elif name == "insider":
            specialists.append(InsiderSpecialist(warehouse(cfg), model))
        elif name:
            raise ValueError(f"unknown specialist {name!r} in SPECIALISTS")
    if not specialists:
        raise RuntimeError("no specialist could be built. Check SPECIALISTS and the indexes")
    return Assistant(specialists, model)
