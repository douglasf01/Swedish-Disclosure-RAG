"""SQLite store: one file with one table per source.

    releases         press releases from Nasdaq, keyed by the exchange's disclosure id
    reports          report PDFs attached to those releases, keyed by the attachment id
    insider_trades   rows from Finansinspektionen's insider register

Ingestion writes here and nothing else does. The indexes and the warehouse table are
derived from this file, so they can always be rebuilt from it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Release:
    id: int
    company: str
    headline: str
    category: str
    language: str
    published: str  # ISO 8601, UTC
    url: str
    text: str | None = None  # None until the body has been fetched
    event: str | None = None  # event tag, set by the tagger


@dataclass(frozen=True)
class Report:
    id: str  # Nasdaq attachment id
    release_id: int  # the release the PDF was attached to
    company: str
    headline: str
    category: str
    published: str  # ISO 8601, UTC
    url: str
    file_name: str
    text: str | None = None  # pages separated by PAGE_BREAK, None until fetched


PAGE_BREAK = "\f"


@dataclass(frozen=True)
class Column:
    name: str
    kind: str  # "text", "number", "flag", "date" or "timestamp"
    doc: str


# The insider-trade columns are defined once, here. The SQLite table, the warehouse table
# and the description shown to the model are all generated from this list.
TRADE_COLUMNS = [
    Column("published", "timestamp", "when the notification was published by the authority"),
    Column("issuer", "text", "company whose instrument was traded, e.g. 'SkiStar AB'"),
    Column("lei", "text", "the issuer's LEI code"),
    Column("person", "text", "the person discharging managerial responsibilities"),
    Column("notifier", "text", "who made the trade: the person, or someone closely associated"),
    Column("position", "text", "e.g. 'Verkställande direktör (VD)', 'Styrelseledamot'"),
    Column("closely_associated", "flag", "true when the notifier is a closely associated party"),
    Column("correction", "flag", "true when the row corrects an earlier notification"),
    Column("share_program", "flag", "true when the trade is part of a share or option program"),
    Column("nature", "text", "e.g. 'Förvärv' (purchase), 'Avyttring' (sale), 'Teckning'"),
    Column("instrument_type", "text", "e.g. 'Aktie', 'Teckningsoption', 'Option'"),
    Column("instrument_name", "text", "name of the instrument, e.g. 'SkiStar AB ser. B'"),
    Column("isin", "text", "ISIN of the instrument"),
    Column("transaction_date", "date", "the day the trade was made"),
    Column("volume", "number", "quantity traded, in `volume_unit`"),
    Column("volume_unit", "text", "'Antal' (number of instruments) or 'Belopp' (amount)"),
    Column("price", "number", "price per instrument, in `currency`"),
    Column("currency", "text", "e.g. 'SEK'"),
    Column("venue", "text", "trading venue, or 'Utanför handelsplats'"),
    Column("status", "text", "'Aktuell' (current) or 'Reviderad' (replaced by a correction)"),
]
TRADES_TABLE = "insider_trades"
SQLITE_TYPES = {
    "text": "TEXT",
    "number": "REAL",
    "flag": "INTEGER",
    "date": "TEXT",
    "timestamp": "TEXT",
}

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS releases (
    id          INTEGER PRIMARY KEY,
    company     TEXT NOT NULL,
    headline    TEXT NOT NULL,
    category    TEXT NOT NULL,         -- category set by the exchange
    language    TEXT NOT NULL,
    published   TEXT NOT NULL,
    url         TEXT NOT NULL,
    text        TEXT,
    event       TEXT
);
CREATE INDEX IF NOT EXISTS releases_published ON releases (published);
CREATE INDEX IF NOT EXISTS releases_company ON releases (company);

CREATE TABLE IF NOT EXISTS reports (
    id          TEXT PRIMARY KEY,
    release_id  INTEGER NOT NULL,
    company     TEXT NOT NULL,
    headline    TEXT NOT NULL,
    category    TEXT NOT NULL,
    published   TEXT NOT NULL,
    url         TEXT NOT NULL,
    file_name   TEXT NOT NULL,
    text        TEXT
);

CREATE TABLE IF NOT EXISTS {TRADES_TABLE} (
    {", ".join(f"{c.name} {SQLITE_TYPES[c.kind]}" for c in TRADE_COLUMNS)}
);
CREATE INDEX IF NOT EXISTS trades_published ON {TRADES_TABLE} (published);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


# --- press releases ---------------------------------------------------------------------


def add_releases(con: sqlite3.Connection, releases: list[Release]) -> int:
    """Insert releases that are not stored yet. Returns the number of new rows."""
    before = con.total_changes
    con.executemany(
        "INSERT OR IGNORE INTO releases (id, company, headline, category, language, published, url)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (r.id, r.company, r.headline, r.category, r.language, r.published, r.url)
            for r in releases
        ],
    )
    con.commit()
    return con.total_changes - before


def set_release_text(con: sqlite3.Connection, release_id: int, text: str) -> None:
    con.execute("UPDATE releases SET text = ? WHERE id = ?", (text, release_id))


def releases_without_text(con: sqlite3.Connection) -> list[Release]:
    rows = con.execute("SELECT * FROM releases WHERE text IS NULL ORDER BY published DESC")
    return [Release(**dict(r)) for r in rows]


def releases_with_text(con: sqlite3.Connection) -> list[Release]:
    rows = con.execute("SELECT * FROM releases WHERE text IS NOT NULL AND text != '' ORDER BY id")
    return [Release(**dict(r)) for r in rows]


def latest_release(con: sqlite3.Connection) -> str | None:
    """Publication time of the newest stored release."""
    return con.execute("SELECT max(published) FROM releases").fetchone()[0]


# --- reports ----------------------------------------------------------------------------


def add_reports(con: sqlite3.Connection, reports: list[Report]) -> int:
    """Insert reports that are not stored yet. Returns the number of new rows."""
    before = con.total_changes
    con.executemany(
        "INSERT OR IGNORE INTO reports"
        " (id, release_id, company, headline, category, published, url, file_name)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (r.id, r.release_id, r.company, r.headline, r.category, r.published, r.url, r.file_name)
            for r in reports
        ],
    )
    con.commit()
    return con.total_changes - before


def set_report_text(con: sqlite3.Connection, report_id: str, text: str) -> None:
    """Store the extracted text. An empty string marks a PDF that could not be read."""
    con.execute("UPDATE reports SET text = ? WHERE id = ?", (text, report_id))


def reports_without_text(con: sqlite3.Connection) -> list[Report]:
    rows = con.execute("SELECT * FROM reports WHERE text IS NULL ORDER BY published DESC")
    return [Report(**dict(r)) for r in rows]


def reports_with_text(con: sqlite3.Connection) -> list[Report]:
    rows = con.execute("SELECT * FROM reports WHERE text IS NOT NULL AND text != '' ORDER BY id")
    return [Report(**dict(r)) for r in rows]


# --- insider trades ---------------------------------------------------------------------


def replace_trades(con: sqlite3.Connection, since: str, until: str, trades: list[dict]) -> None:
    """Replace every trade published in [since, until) with `trades`. Times are ISO 8601, UTC.

    The register has no row id and rows are revised after publication, so a time window
    is always rewritten as a whole.
    """
    names = [c.name for c in TRADE_COLUMNS]
    con.execute(
        f"DELETE FROM {TRADES_TABLE} WHERE published >= ? AND published < ?", (since, until)
    )
    con.executemany(
        f"INSERT INTO {TRADES_TABLE} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
        [[t[n] for n in names] for t in trades],
    )
    con.commit()


def trades(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(f"SELECT * FROM {TRADES_TABLE} ORDER BY published")
    return [dict(r) for r in rows]


def latest_trade(con: sqlite3.Connection) -> str | None:
    """Publication time of the newest stored insider trade."""
    return con.execute(f"SELECT max(published) FROM {TRADES_TABLE}").fetchone()[0]
