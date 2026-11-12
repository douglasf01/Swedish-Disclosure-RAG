"""HTTP and text helpers shared by the sources."""

from __future__ import annotations

import re
import time
import urllib.request

HEADERS = {"User-Agent": "Mozilla/5.0 (press release research project)"}


def get(url: str, retries: int = 3) -> bytes:
    """GET with a few retries on network errors."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def clean_lines(text: str) -> str:
    """Collapse runs of spaces, turn non-breaking spaces into plain ones, drop empty lines."""
    lines = (
        re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\xa0", " ").splitlines()
    )
    return "\n".join(line for line in lines if line)
