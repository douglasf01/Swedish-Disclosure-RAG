"""Command line for ingestion. See the package docstring for what is fetched."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from .. import store
from ..config import Settings
from . import SOURCES, default_since, run


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch new data into the SQLite store.")
    ap.add_argument("--since", help="YYYY-MM-DD, default is the newest stored release")
    ap.add_argument("--until", help="YYYY-MM-DD, default is now")
    ap.add_argument(
        "--only", nargs="+", choices=SOURCES, default=list(SOURCES), help="sources to fetch"
    )
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between requests to Nasdaq")
    ap.add_argument("--limit", type=int, help="most release bodies and report PDFs to fetch")
    args = ap.parse_args()

    con = store.connect(Settings().db_path)
    until = (
        datetime.fromisoformat(args.until).replace(tzinfo=UTC) if args.until else datetime.now(UTC)
    )
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
    else:
        since = default_since(con)
    new = run(con, since, until, tuple(args.only), args.delay, args.limit)
    print(f"done, new rows: {new}")


if __name__ == "__main__":
    main()
