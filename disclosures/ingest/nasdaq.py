"""Nasdaq Nordic's company news service: HTTP access and the release listing.

The listing endpoint is the one Nasdaq's own news pages call. It is not a documented
public API, so requests are throttled and only what is needed is fetched: Swedish-language
releases from companies on the Stockholm main market. One listing serves two sources: the
releases themselves (`press`) and the PDFs attached to them (`reports`).
"""

from __future__ import annotations

import json
import time
import urllib.parse
from collections.abc import Iterator
from datetime import UTC, datetime

from .common import get

LISTING = "https://api.news.eu.nasdaq.com/news/query.action"
MARKET = "Main Market, Stockholm"
PAGE = 200  # the service returns at most 200 items per request
MAX_START = 10_000  # and refuses offsets from 10 000 upwards


def to_iso(stamp: str) -> str:
    """'2026-11-11 17:25:47 +0000' -> '2026-11-11T17:25:47Z'"""
    return (
        datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S %z")
        .astimezone(UTC)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def items(payload: dict) -> list[dict]:
    """The releases in one page of the listing."""
    return payload.get("results", {}).get("item", [])


def listing(since: datetime, until: datetime, language: str = "sv") -> Iterator[dict]:
    """Pages of the listing for releases published in [since, until], newest first.

    Use windows of about a month: the service refuses to page past 10 000 releases.
    """
    params = {
        "type": "json",
        "showAttachments": "true",
        "showCnsSpecific": "true",
        "showCompany": "true",
        "countResults": "false",
        "globalGroup": "exchangeNotice",
        "globalName": "NordicMainMarkets",
        "market": MARKET,
        "displayLanguage": language,
        "language": language,
        "dir": "DESC",
        "limit": str(PAGE),
        "fromDate": str(int(since.timestamp() * 1000)),
        "toDate": str(int(until.timestamp() * 1000)),
    }
    for start in range(0, MAX_START, PAGE):
        url = LISTING + "?" + urllib.parse.urlencode({**params, "start": str(start)})
        payload = json.loads(get(url))
        yield payload
        if len(items(payload)) < PAGE:
            return
        time.sleep(0.5)
    raise RuntimeError("date window holds 10 000 releases or more. Use a shorter window")
