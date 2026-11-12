"""Blob storage: where the data files live between runs.

The service has no database server. The daily job writes the SQLite store and the index
files to a bucket, and each service instance downloads the indexes when it starts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..config import Settings


class BlobStore(Protocol):
    """Mirrors files between a local directory and a bucket, keeping their relative names."""

    def download(self, names: list[str], data_dir: Path) -> list[str]:
        """Download the files that exist in the bucket. Returns the names that were found."""
        ...

    def upload(self, names: list[str], data_dir: Path) -> None: ...


class GcsBlobStore:
    """Google Cloud Storage."""

    def __init__(self, bucket: str) -> None:
        from google.cloud import storage

        self.bucket = storage.Client().bucket(bucket)

    def download(self, names: list[str], data_dir: Path) -> list[str]:
        found = []
        for name in names:
            blob = self.bucket.blob(name)
            if blob.exists():
                target = data_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                blob.download_to_filename(str(target))
                found.append(name)
        return found

    def upload(self, names: list[str], data_dir: Path) -> None:
        for name in names:
            self.bucket.blob(name).upload_from_filename(str(data_dir / name))


def blob_store(cfg: Settings) -> BlobStore | None:
    """The configured bucket, or None when the data lives in local files only."""
    if not cfg.bucket:
        return None
    if cfg.storage_provider == "gcs":
        return GcsBlobStore(cfg.bucket)
    raise ValueError(f"unknown STORAGE_PROVIDER {cfg.storage_provider!r}. Add it in {__name__}")
