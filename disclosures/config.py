"""Settings, read from environment variables so the same image runs locally and in any cloud.

Three settings choose the implementation of a cloud service (see `disclosures/cloud/`):
`LLM_PROVIDER`, `STORAGE_PROVIDER` and `WAREHOUSE`. Everything else is plain configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    # Data: local files, mirrored to a bucket when one is configured.
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")))
    bucket: str = field(default_factory=lambda: _env("DATA_BUCKET", ""))  # empty = local files only
    storage_provider: str = field(default_factory=lambda: _env("STORAGE_PROVIDER", "gcs"))

    # Embeddings run in-process on CPU, so they are the same in every environment.
    embed_model: str = field(
        default_factory=lambda: _env("EMBED_MODEL", "intfloat/multilingual-e5-small")
    )
    model_cache: str = field(default_factory=lambda: _env("MODEL_CACHE", "models"))

    # Language model.
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "google"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "gemini-3.5-flash-lite"))
    api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY", ""))  # empty = Vertex AI
    project: str = field(default_factory=lambda: _env("GOOGLE_CLOUD_PROJECT", ""))
    location: str = field(default_factory=lambda: _env("VERTEX_LOCATION", "global"))
    # USD per million tokens, used only for the cost estimate in the logs.
    price_in: float = field(default_factory=lambda: float(_env("PRICE_IN_PER_M", "0.30")))
    price_out: float = field(default_factory=lambda: float(_env("PRICE_OUT_PER_M", "2.50")))

    # Warehouse for the insider-trade table: "sqlite" reads the local store, "bigquery" a dataset.
    warehouse: str = field(default_factory=lambda: _env("WAREHOUSE", "sqlite"))
    warehouse_dataset: str = field(default_factory=lambda: _env("WAREHOUSE_DATASET", "disclosures"))

    # Which specialists the assistant may consult, comma-separated.
    specialists: str = field(default_factory=lambda: _env("SPECIALISTS", "press,reports,insider"))

    # Spending guards for the public endpoint.
    daily_cap: int = field(default_factory=lambda: int(_env("DAILY_QUESTION_CAP", "50")))
    per_ip_per_min: int = field(default_factory=lambda: int(_env("PER_IP_PER_MINUTE", "6")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "releases.db"

    def index_dir(self, corpus: str) -> Path:
        return self.data_dir / "index" / corpus
