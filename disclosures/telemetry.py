"""Structured logs. Cloud Run turns each JSON line on stdout into a searchable log entry,
and the log-based metrics in infra/monitoring.tf read latency, tokens and cost from them."""

from __future__ import annotations

import json
import sys


def log(event: str, severity: str = "INFO", **fields) -> None:
    print(
        json.dumps({"severity": severity, "message": event, **fields}, ensure_ascii=False),
        file=sys.stdout,
        flush=True,
    )


def cost_usd(tokens_in: int, tokens_out: int, price_in: float, price_out: float) -> float:
    """Estimate from list prices per million tokens. The invoice is the source of truth."""
    return round((tokens_in * price_in + tokens_out * price_out) / 1_000_000, 6)
