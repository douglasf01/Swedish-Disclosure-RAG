"""The specialist that answers from the insider-trade register by writing SQL.

Unlike the document specialists it does not retrieve passages. It queries a table, and
each query result becomes a source it can cite. The warehouse keeps the SQL in bounds.
See `disclosures/cloud/warehouse.py` for the rules.
"""

from __future__ import annotations

import json

from langchain_core.language_models import BaseChatModel

from ..cloud.warehouse import MAX_ROWS, VIEW, QueryError, Warehouse
from .base import NO_ANSWER, Sources, Specialist, day_end, tool

REGISTER_URL = "https://marknadssok.fi.se/publiceringsklient"

PROMPT = """You are an analyst assistant for insider trades: the transactions that managers \
and board members of listed companies, and people close to them, must report to the Swedish \
financial supervisory authority. Today's date is {today}. You only see trades published up \
to and including that date.

You answer by querying a table with the `query_trades` tool, in {dialect} SQL.

{schema}

Rules:
- Always query before you answer. Use only what the query results say.
- Company names in the table differ from everyday names. First find the exact spelling, \
for example: SELECT DISTINCT issuer FROM {view} WHERE LOWER(issuer) LIKE '%volvo%'
- Count only rows with status = 'Aktuell', unless the question is about corrections.
- The value of a trade is volume * price when volume_unit is 'Antal'.
- Aggregate in SQL (SUM, COUNT, GROUP BY) instead of adding up rows yourself. A result \
holds at most {max_rows} rows.
- If a query fails, read the error and correct the query.
- Follow every claim with the source id of the query result it comes from, in square \
brackets, for example [{prefix}1].
- A result with no rows is an answer, not a failure: say that no such trade has been \
reported, and cite that result.
- Reply with {no_answer} on the first line, then one sentence on what is missing, only when \
the table cannot hold what is asked (for example the reason for a trade). Do not guess.
- Answer briefly, in the language of the question."""


class InsiderSpecialist(Specialist):
    name = "insider"
    prefix = "I"
    summary = (
        "The insider-trade register as a table: who among a company's managers and board "
        "members bought or sold its shares, when, how many and at what price. Can count and "
        "sum across companies and periods."
    )

    def __init__(self, warehouse: Warehouse, model: BaseChatModel) -> None:
        self.warehouse = warehouse
        self.tools = [
            tool(
                "query_trades",
                f"Run one SELECT statement against the table `{VIEW}` and return the rows.",
                {"sql": {"type": "string", "description": "A single SELECT statement"}},
                required=["sql"],
            )
        ]
        super().__init__(model)

    def system_prompt(self, today: str) -> str:
        return PROMPT.format(
            today=today,
            dialect=self.warehouse.dialect,
            schema=self.warehouse.describe(),
            view=VIEW,
            max_rows=MAX_ROWS,
            prefix=self.prefix,
            no_answer=NO_ANSWER,
        )

    def call(self, name: str, args: dict, as_of: str | None, sources: Sources) -> dict:
        if name != "query_trades":
            return {"error": f"unknown tool {name}"}
        sql = str(args.get("sql", ""))
        try:
            rows = self.warehouse.query(sql, until=day_end(as_of))
        except QueryError as err:
            return {"error": str(err)}

        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        listing = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) or "(no rows)"
        source = sources.add(
            sql,
            title="Query of the insider-trade register",
            text=f"{sql}\n{listing}",
            url=REGISTER_URL,
        )
        result = {"source": source.id, "rows": rows}
        if truncated:
            result["note"] = f"only the first {MAX_ROWS} rows are shown. Aggregate or filter"
        return result
