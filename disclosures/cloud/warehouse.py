"""SQL warehouse: where the insider-trade table is queried.

The insider specialist writes its own SQL, so this module is also where that SQL is kept
in bounds. Three rules hold for every implementation:

* Read only. A query must be a single SELECT, and the connection or service account can
  only read.
* Point in time. The model never sees the stored table. Its query runs against `trades`,
  a view that the warehouse defines around the query and that holds only the rows
  published up to the question's date.
* Bounded. At most MAX_ROWS rows are returned.

Locally the warehouse is the SQLite store itself. In the cloud the daily job copies the
table to BigQuery, so service instances can stay stateless.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from ..config import Settings
from ..store import SQLITE_TYPES, TRADE_COLUMNS, TRADES_TABLE

VIEW = "trades"  # the only table name a query may use
MAX_ROWS = 50
NO_CUTOFF = "9999-12-31T23:59:59Z"
BIGQUERY_TYPES = {
    "text": "STRING",
    "number": "FLOAT64",
    "flag": "BOOL",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
}


class QueryError(Exception):
    """A query that was rejected or failed. The message is written for the model to act on."""


class Warehouse(Protocol):
    dialect: str  # named in the model's prompt, e.g. "SQLite"

    def describe(self) -> str:
        """The `trades` view as the model should see it: columns, types and conventions."""
        ...

    def query(self, sql: str, until: str | None = None) -> list[dict]:
        """Run one SELECT over the trades published up to `until` (ISO 8601, UTC).

        Returns at most MAX_ROWS + 1 rows, so the caller can tell a cut-off result.
        Raises QueryError for anything the model should correct.
        """
        ...

    def load(self, trades: list[dict]) -> None:
        """Replace the table with `trades`, rows keyed by the names in TRADE_COLUMNS."""
        ...


def check(sql: str) -> str:
    """The query without trailing semicolon, or a QueryError saying what to change."""
    sql = sql.strip().rstrip(";").strip()
    if not re.match(r"select\b", sql, re.IGNORECASE):
        raise QueryError("Only a single statement starting with SELECT is allowed.")
    if ";" in sql:
        raise QueryError("Only one statement is allowed. Remove the semicolon.")
    if re.search(rf"\b{TRADES_TABLE}\b", sql, re.IGNORECASE):
        raise QueryError(f"Query the table `{VIEW}`. `{TRADES_TABLE}` is not available.")
    return sql


def _columns(types: dict[str, str]) -> str:
    return "\n".join(f"  {c.name} {types[c.kind]}: {c.doc}" for c in TRADE_COLUMNS)


def _plain(value):
    """Database values as JSON-friendly ones."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


class SqliteWarehouse:
    """Queries the insider-trade table of the local SQLite store, read-only."""

    dialect = "SQLite"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def describe(self) -> str:
        return (
            f"Table `{VIEW}`:\n{_columns(SQLITE_TYPES)}\n"
            "Flags are 0 or 1. Dates ('YYYY-MM-DD') and timestamps ('YYYY-MM-DDTHH:MM:SSZ', "
            "UTC) are text in ISO 8601 form, so they compare correctly as strings."
        )

    def query(self, sql: str, until: str | None = None) -> list[dict]:
        scoped = f"WITH {VIEW} AS (SELECT * FROM {TRADES_TABLE} WHERE published <= ?) {check(sql)}"
        # A new read-only connection per query: cheap, and safe to call from several threads.
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(scoped, (until or NO_CUTOFF,)).fetchmany(MAX_ROWS + 1)
        except sqlite3.Error as err:
            raise QueryError(f"SQLite error: {err}") from err
        finally:
            con.close()
        return [dict(r) for r in rows]

    def load(self, trades: list[dict]) -> None:
        """Nothing to do: ingestion already wrote the trades to the store this reads."""


class BigQueryWarehouse:
    """Queries a BigQuery table that the daily job replaces after each ingestion."""

    dialect = "BigQuery (GoogleSQL)"
    max_bytes_billed = 100 * 1024**2  # a query that would scan more than this is refused

    def __init__(self, project: str, dataset: str, client=None) -> None:
        from google.cloud import bigquery

        self.bq = bigquery
        self.client = client or bigquery.Client(project=project or None)
        self.table = f"{self.client.project}.{dataset}.{TRADES_TABLE}"

    def describe(self) -> str:
        return (
            f"Table `{VIEW}`:\n{_columns(BIGQUERY_TYPES)}\n"
            "Compare `transaction_date` with DATE literals such as DATE '2026-10-01' and "
            "`published` with TIMESTAMP literals. Flags are TRUE or FALSE."
        )

    def query(self, sql: str, until: str | None = None) -> list[dict]:
        from google.api_core.exceptions import GoogleAPICallError

        scoped = (
            f"WITH {VIEW} AS (SELECT * FROM `{self.table}` WHERE published <= @until) {check(sql)}"
        )
        cutoff = datetime.fromisoformat(until or NO_CUTOFF)
        config = self.bq.QueryJobConfig(
            query_parameters=[self.bq.ScalarQueryParameter("until", "TIMESTAMP", cutoff)],
            maximum_bytes_billed=self.max_bytes_billed,
        )
        try:
            rows = self.client.query(scoped, job_config=config).result(max_results=MAX_ROWS + 1)
            return [{key: _plain(value) for key, value in row.items()} for row in rows]
        except GoogleAPICallError as err:
            raise QueryError(f"BigQuery error: {err.message}") from err

    def load(self, trades: list[dict]) -> None:
        flags = [c.name for c in TRADE_COLUMNS if c.kind == "flag"]
        rows = [
            {
                **trade,
                **{name: bool(trade[name]) for name in flags},
                "transaction_date": trade["transaction_date"] or None,
            }
            for trade in trades
        ]
        config = self.bq.LoadJobConfig(
            schema=[self.bq.SchemaField(c.name, BIGQUERY_TYPES[c.kind]) for c in TRADE_COLUMNS],
            write_disposition=self.bq.WriteDisposition.WRITE_TRUNCATE,
        )
        self.client.load_table_from_json(rows, self.table, job_config=config).result()


def warehouse(cfg: Settings) -> Warehouse:
    if cfg.warehouse == "sqlite":
        return SqliteWarehouse(cfg.db_path)
    if cfg.warehouse == "bigquery":
        return BigQueryWarehouse(cfg.project, cfg.warehouse_dataset)
    raise ValueError(f"unknown WAREHOUSE {cfg.warehouse!r}. Add it in {__name__}")
