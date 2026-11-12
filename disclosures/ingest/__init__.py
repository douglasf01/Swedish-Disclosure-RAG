"""Ingestion: fetch new data from the three sources into the SQLite store.

press     press releases from Nasdaq's company news service
reports   the report PDFs attached to those releases
insider   insider trades from Finansinspektionen's register

uv run python -m disclosures.ingest --since 2026-08-01     # backfill
uv run python -m disclosures.ingest                        # everything since the last run
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from .. import store
from . import insider, nasdaq, press, reports

SOURCES = ("press", "reports", "insider")
WINDOW = timedelta(days=30)  # keeps each listing query far below Nasdaq's paging limit


def default_since(con: sqlite3.Connection, fallback_days: int = 7) -> datetime:
    """Where an incremental run starts: at the newest stored release."""
    if latest := store.latest_release(con):
        return datetime.fromisoformat(latest)
    return datetime.now(UTC) - timedelta(days=fallback_days)


def run(
    con: sqlite3.Connection,
    since: datetime,
    until: datetime,
    sources: tuple[str, ...] = SOURCES,
    delay: float = 0.5,
    limit: int | None = None,
) -> dict[str, int]:
    """Fetch everything published in [since, until]. Returns the new rows per source.

    `delay` is the pause between requests to Nasdaq (the insider register has its own,
    longer pause). `limit` caps the number of release bodies and report PDFs fetched.
    """
    new = dict.fromkeys(sources, 0)
    if "press" in sources or "reports" in sources:
        start = since
        while start < until:
            end = min(start + WINDOW, until)
            for page in nasdaq.listing(start, end):
                if "press" in sources:
                    new["press"] += store.add_releases(con, press.parse_listing(page))
                if "reports" in sources:
                    new["reports"] += store.add_reports(con, reports.parse_listing(page))
            print(f"listed {start:%Y-%m-%d} to {end:%Y-%m-%d}: {new}", flush=True)
            start = end
        if "press" in sources:
            press.fetch_bodies(con, delay, limit)
        if "reports" in sources:
            reports.fetch_texts(con, delay, limit)
    if "insider" in sources:
        first = since.astimezone(insider.STOCKHOLM).date()
        last = until.astimezone(insider.STOCKHOLM).date()
        new["insider"] = insider.fetch(con, first, last)
    return new
