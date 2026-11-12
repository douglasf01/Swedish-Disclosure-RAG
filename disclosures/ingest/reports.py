"""Financial reports: the PDFs attached to interim, half-year and annual report releases.

A report release usually carries the full report as a PDF, and sometimes the press release
as a second PDF. Both are kept, because telling them apart by file name is not reliable.
"""

from __future__ import annotations

import io
import logging
import sqlite3
import time

from .. import store
from ..store import PAGE_BREAK, Report
from . import nasdaq
from .common import clean_lines, get

REPORT_WORDS = ("rapport", "bokslut", "årsredovisning")  # matched against the exchange category
MAX_PAGES = 60  # an interim report is 20 to 40 pages, annual reports are cut off here

logging.getLogger("pypdf").setLevel(logging.ERROR)  # it warns about every odd font


def is_report(category: str) -> bool:
    return any(word in category.lower() for word in REPORT_WORDS)


def parse_listing(payload: dict) -> list[Report]:
    """The PDF attachments of every report release in one page of the listing."""
    return [
        Report(
            id=att["attachmentUrl"].rstrip("/").rsplit("/", 1)[-1],
            release_id=int(item["disclosureId"]),
            company=item.get("company", "").strip(),
            headline=item.get("headline", "").strip(),
            category=item.get("cnsCategory", "").strip(),
            published=nasdaq.to_iso(item["published"]),
            url=att["attachmentUrl"],
            file_name=att.get("fileName", ""),
        )
        for item in nasdaq.items(payload)
        if is_report(item.get("cnsCategory", ""))
        for att in item.get("attachment") or []
        if att.get("mimetype") == "application/pdf"
    ]


def pdf_text(data: bytes, max_pages: int = MAX_PAGES) -> str:
    """Text of the first `max_pages` pages, separated by form feeds so page numbers survive.

    A page pypdf cannot read (usually one that is a single huge drawing) becomes an empty page.
    """
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    def page_text(page) -> str:
        try:
            return clean_lines(page.extract_text() or "")
        except PyPdfError:
            return ""

    pages = [page_text(page) for page in PdfReader(io.BytesIO(data)).pages[:max_pages]]
    return PAGE_BREAK.join(pages) if any(pages) else ""


def fetch_texts(con: sqlite3.Connection, delay: float, limit: int | None = None) -> int:
    """Download and read every report that has no text yet. Returns how many were tried."""
    todo = store.reports_without_text(con)[:limit]
    for n, rep in enumerate(todo, start=1):
        try:
            text = pdf_text(get(rep.url))
        except OSError as err:  # network trouble: leave it for the next run
            print(f"  skipped report {rep.id}: {err}")
            continue
        except Exception as err:  # noqa: BLE001  a broken PDF must not stop the whole run,
            # and it will not parse next time either, so it is stored as empty.
            print(f"  unreadable report {rep.id} ({rep.file_name}): {err!r}"[:200])
            text = ""
        store.set_report_text(con, rep.id, text)
        if n % 10 == 0:
            con.commit()
            print(f"  reports {n}/{len(todo)}", flush=True)
        time.sleep(delay)
    con.commit()
    return len(todo)
