"""Insider trades from Finansinspektionen's public register (insynsregistret).

The register's search page can export its result as CSV. An export holds at most 1 000
rows and the site drops clients that ask too often, so it is fetched a week at a time with
a pause in between, and a week that hits the row limit is split in two. The register
covers every issuer with instruments traded in Sweden, not only the Stockholm main market.
"""

from __future__ import annotations

import csv
import io
import sqlite3
import time
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import store
from .common import clean_lines, get

EXPORT = "https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search"
EXPORT_LIMIT = 1000  # rows per export
WINDOW_DAYS = 7
DELAY = 3.0  # seconds between exports
STOCKHOLM = ZoneInfo("Europe/Stockholm")  # the register states times in Swedish local time

# Register column -> (our column, converter). Columns not listed here are dropped.
_TEXT = "text"
_FLAG = "flag"
_NUMBER = "number"
_FIELDS = {
    "Emittent": ("issuer", _TEXT),
    "LEI-kod": ("lei", _TEXT),
    "Person i ledande ställning": ("person", _TEXT),
    "Anmälningsskyldig": ("notifier", _TEXT),
    "Befattning": ("position", _TEXT),
    "Närstående": ("closely_associated", _FLAG),
    "Korrigering": ("correction", _FLAG),
    "Är kopplad till aktieprogram": ("share_program", _FLAG),
    "Karaktär": ("nature", _TEXT),
    "Instrumenttyp": ("instrument_type", _TEXT),
    "Instrumentnamn": ("instrument_name", _TEXT),
    "ISIN": ("isin", _TEXT),
    "Volym": ("volume", _NUMBER),
    "Volymsenhet": ("volume_unit", _TEXT),
    "Pris": ("price", _NUMBER),
    "Valuta": ("currency", _TEXT),
    "Handelsplats": ("venue", _TEXT),
    "Status": ("status", _TEXT),
}


def _utc(local: datetime) -> str:
    return local.replace(tzinfo=STOCKHOLM).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value: str) -> float | None:
    """'1 229,50' -> 1229.5"""
    value = value.replace(" ", "").replace(",", ".")
    return float(value) if value else None


def _convert(value: str, kind: str):
    value = clean_lines(value)
    if kind == _FLAG:
        return int(value == "Ja")
    if kind == _NUMBER:
        return _number(value)
    return value


def parse_export(raw: bytes) -> list[dict]:
    """Rows of one CSV export (UTF-16, semicolon-separated) as dicts with our column names."""
    rows = csv.DictReader(io.StringIO(raw.decode("utf-16")), delimiter=";")
    trades = []
    for row in rows:
        trade = {name: _convert(row.get(src) or "", kind) for src, (name, kind) in _FIELDS.items()}
        trade["published"] = _utc(datetime.fromisoformat(row["Publiceringsdatum"]))
        trade["transaction_date"] = (row.get("Transaktionsdatum") or "")[:10]
        trades.append(trade)
    return trades


def export(first: date, last: date) -> list[dict]:
    """The trades published from `first` to `last` (Swedish local dates, both included).

    Cut off without warning at EXPORT_LIMIT rows. `fetch` checks for that.
    """
    query = urllib.parse.urlencode(
        {
            "SearchFunctionType": "Insyn",
            "Publiceringsdatum.From": first.isoformat(),
            "Publiceringsdatum.To": last.isoformat(),
            "button": "export",
            "Page": "1",
        }
    )
    return parse_export(get(f"{EXPORT}?{query}"))


def fetch(con: sqlite3.Connection, since: date, until: date, delay: float = DELAY) -> int:
    """Store the trades published from `since` to `until`, both included. Returns the row count.

    Each window is replaced as a whole, so running it again over the same days is safe and
    picks up rows the authority has revised since.
    """
    total = 0
    first, days = since, WINDOW_DAYS
    while first <= until:
        last = min(first + timedelta(days=days - 1), until)
        trades = export(first, last)
        time.sleep(delay)
        if len(trades) >= EXPORT_LIMIT:
            if first == last:
                raise RuntimeError(f"more than {EXPORT_LIMIT} trades were published on {first}")
            days = max(1, (last - first).days // 2)
            continue
        start = datetime.combine(first, datetime.min.time())
        end = datetime.combine(last + timedelta(days=1), datetime.min.time())
        store.replace_trades(con, _utc(start), _utc(end), trades)
        print(f"  insider trades {first} to {last}: {len(trades)}", flush=True)
        total += len(trades)
        first, days = last + timedelta(days=1), WINDOW_DAYS
    return total
