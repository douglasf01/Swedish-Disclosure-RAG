"""The warehouse rules: read only, point in time, bounded. And the BigQuery adapter's SQL."""

from types import SimpleNamespace

import pytest

from disclosures.cloud.warehouse import MAX_ROWS, BigQueryWarehouse, QueryError, check

from .conftest import TRADES


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE trades",
        "WITH t AS (SELECT 1) SELECT * FROM t",
        "SELECT 1; DELETE FROM trades",
        "SELECT * FROM insider_trades",
        'SELECT * FROM main."INSIDER_TRADES"',
    ],
)
def test_check_rejects_what_is_not_a_single_select_over_the_view(sql):
    with pytest.raises(QueryError):
        check(sql)


def test_check_accepts_a_select_and_drops_the_semicolon():
    assert check("  select * from trades; ") == "select * from trades"


def test_query_sees_only_trades_published_up_to_the_cutoff(warehouse):
    count = "SELECT COUNT(*) AS n FROM trades"
    assert warehouse.query(count) == [{"n": 3}]
    assert warehouse.query(count, until="2026-09-15T23:59:59Z") == [{"n": 2}]
    assert warehouse.query(count, until="2026-01-01T00:00:00Z") == [{"n": 0}]
    # A subquery cannot get around the view either.
    nested = "SELECT (SELECT COUNT(*) FROM trades) AS n FROM trades LIMIT 1"
    assert warehouse.query(nested, until="2026-09-15T23:59:59Z") == [{"n": 2}]


def test_query_is_read_only_and_bounded(warehouse):
    with pytest.raises(QueryError, match="SQLite error"):
        warehouse.query("SELECT * FROM trades WHERE no_such_column = 1")
    many = "SELECT a.issuer FROM trades a, trades b, trades c, trades d, trades e"
    assert len(warehouse.query(many)) == MAX_ROWS + 1


class FakeBigQuery:
    """Records what the adapter sends to BigQuery and returns canned rows."""

    project = "proj"

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries = []
        self.loads = []

    def query(self, sql, job_config):
        self.queries.append((sql, job_config))
        return SimpleNamespace(result=lambda max_results: self.rows)

    def load_table_from_json(self, rows, table, job_config):
        self.loads.append((rows, table, job_config))
        return SimpleNamespace(result=lambda: None)


def test_bigquery_scopes_the_query_and_caps_the_bytes():
    from datetime import UTC, date, datetime

    rows = [
        {"published": datetime(2026, 9, 15, 9, tzinfo=UTC), "transaction_date": date(2026, 9, 14)}
    ]
    client = FakeBigQuery(rows)
    wh = BigQueryWarehouse("proj", "ds", client=client)
    result = wh.query(
        "SELECT published, transaction_date FROM trades", until="2026-09-30T23:59:59Z"
    )
    assert result == [{"published": "2026-09-15T09:00:00Z", "transaction_date": "2026-09-14"}]

    sql, config = client.queries[0]
    assert sql.startswith(
        "WITH trades AS (SELECT * FROM `proj.ds.insider_trades` WHERE published <= @until) SELECT"
    )
    (param,) = config.query_parameters
    assert (param.name, param.type_, param.value.isoformat()) == (
        "until",
        "TIMESTAMP",
        "2026-09-30T23:59:59+00:00",
    )
    assert config.maximum_bytes_billed == wh.max_bytes_billed


def test_bigquery_load_replaces_the_table_with_typed_rows():
    client = FakeBigQuery()
    BigQueryWarehouse("proj", "ds", client=client).load([TRADES[0] | {"transaction_date": ""}])
    rows, table, config = client.loads[0]
    assert table == "proj.ds.insider_trades"
    assert rows[0]["correction"] is False and rows[0]["transaction_date"] is None
    assert config.write_disposition == "WRITE_TRUNCATE"
    assert {f.name: f.field_type for f in config.schema}["published"] == "TIMESTAMP"
