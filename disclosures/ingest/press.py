"""Press releases: parse the Nasdaq listing and fetch each release body as plain text."""

from __future__ import annotations

import html
import sqlite3
import time
from html.parser import HTMLParser

from .. import store
from ..store import Release
from . import nasdaq
from .common import clean_lines, get

BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table"}


def parse_listing(payload: dict) -> list[Release]:
    return [
        Release(
            id=int(item["disclosureId"]),
            company=item.get("company", "").strip(),
            headline=item.get("headline", "").strip(),
            category=item.get("cnsCategory", "").strip(),
            language=item.get("language", ""),
            published=nasdaq.to_iso(item["published"]),
            url=item["messageUrl"],
        )
        for item in nasdaq.items(payload)
    ]


class _BodyText(HTMLParser):
    """Extracts the text of <div id="view-body"> with one line per block element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0  # nesting depth inside view-body, 0 = outside
        self.skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.depth == 0:
            if tag == "div" and dict(attrs).get("id") == "view-body":
                self.depth = 1
            return
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "div":
            self.depth += 1
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "td":
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if self.depth == 0:
            return
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if tag == "div":
            self.depth -= 1
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.parts.append(data)


def body_text(page: str) -> str:
    parser = _BodyText()
    parser.feed(page)
    return clean_lines(html.unescape("".join(parser.parts)))


def fetch_bodies(con: sqlite3.Connection, delay: float, limit: int | None = None) -> int:
    """Fetch the body of every release that has none yet. Returns how many were tried."""
    todo = store.releases_without_text(con)[:limit]
    for n, rel in enumerate(todo, start=1):
        try:
            text = body_text(get(rel.url).decode("utf-8", errors="replace"))
        except OSError as err:
            print(f"  skipped release {rel.id}: {err}")
            continue
        store.set_release_text(con, rel.id, text)
        if n % 50 == 0:
            con.commit()
            print(f"  bodies {n}/{len(todo)}", flush=True)
        time.sleep(delay)
    con.commit()
    return len(todo)
