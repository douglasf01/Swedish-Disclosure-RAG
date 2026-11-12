"""Daily job: fetch new data, update the indexes and the warehouse, publish to the bucket.

1. download the SQLite store and the indexes from the bucket
2. ingest press releases, reports and insider trades since the last run
3. extend each index with the new passages
4. replace the warehouse table with the insider trades
5. upload the store and the indexes

python -m disclosures.job [--since YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from . import ingest, store
from .cloud.storage import blob_store
from .cloud.warehouse import warehouse
from .config import Settings
from .index import CORPORA, FastEmbedder, index_files, rebuild
from .telemetry import log


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch new data and publish the updated indexes.")
    ap.add_argument("--since", help="backfill from this date instead of the newest stored release")
    args = ap.parse_args()
    cfg = Settings()
    files = [cfg.db_path.name, *index_files(CORPORA)]

    bucket = blob_store(cfg)
    if bucket:
        bucket.download(files, cfg.data_dir)
    con = store.connect(cfg.db_path)

    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
    else:
        since = ingest.default_since(con, fallback_days=30)
    new = ingest.run(con, since, datetime.now(UTC))

    embedder = FastEmbedder(cfg.embed_model, cfg.model_cache)
    passages = {
        corpus: len(rebuild(con, corpus, cfg.index_dir(corpus), embedder).chunks)
        for corpus in CORPORA
    }
    warehouse(cfg).load(store.trades(con))
    con.close()

    if bucket:
        bucket.upload(files, cfg.data_dir)
    log("ingest_done", new=new, passages=passages)


if __name__ == "__main__":
    main()
