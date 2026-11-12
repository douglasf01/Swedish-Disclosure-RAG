"""The two specialists that answer from a search index: press releases and reports.

They share everything but their corpus and wording, so one class does the work and two
small subclasses name the differences.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from ..retrieve import Filters, Retriever
from .base import NO_ANSWER, Sources, Specialist, day_end, tool

PROMPT = """You are an analyst assistant for {documents} from companies listed on Nasdaq \
Stockholm. Today's date is {today}. You only know {documents} published up to and \
including that date.

Rules:
- Always search with the tools before you answer. Use only what the search results say, \
never your own knowledge about the companies.
- The documents are in Swedish. Write every search query in Swedish, translating the \
user's terms when the question is in another language (for example "acting CEO" becomes \
"tillförordnad vd", "net sales" becomes "nettoomsättning").
- If a search finds nothing useful, try once more with other words or with a company filter.
- Follow every claim with source ids in square brackets, for example [{prefix}3] or \
[{prefix}2][{prefix}5].
- If the search results are not enough to answer: reply with {no_answer} on the first line, \
then one sentence on what is missing. Do not guess.
- Answer briefly, in the language of the question. Give amounts and dates as the source \
states them."""


class DocumentSpecialist(Specialist):
    documents: str  # what the corpus holds, in the plural, e.g. "press releases"
    categories: str  # examples of the exchange's categories in this corpus

    def __init__(self, retriever: Retriever, model: BaseChatModel) -> None:
        self.retriever = retriever
        self.tools = [
            tool(
                "search",
                f"Search {self.documents}. Returns passages, each with a source id, company, "
                "headline and publication time. The query must be in Swedish.",
                {
                    "query": {"type": "string", "description": "What to look for"},
                    "company": {
                        "type": "string",
                        "description": "Part of a company name, to restrict the search",
                    },
                    "category": {
                        "type": "string",
                        "description": f"Part of the exchange's category, e.g. {self.categories}",
                    },
                    "since": {
                        "type": "string",
                        "description": "Earliest publication date, YYYY-MM-DD",
                    },
                    "until": {
                        "type": "string",
                        "description": "Latest publication date, YYYY-MM-DD",
                    },
                },
                required=["query"],
            ),
            tool(
                "list_companies",
                "List company names that contain the given text. Use it to find the exact "
                "name before filtering a search by company.",
                {"name": {"type": "string"}},
                required=["name"],
            ),
        ]
        super().__init__(model)

    def system_prompt(self, today: str) -> str:
        return PROMPT.format(
            documents=self.documents, today=today, prefix=self.prefix, no_answer=NO_ANSWER
        )

    def call(self, name: str, args: dict, as_of: str | None, sources: Sources) -> dict:
        if name == "list_companies":
            return {"companies": self.retriever.companies(str(args.get("name", "")), as_of=as_of)}
        if name != "search":
            return {"error": f"unknown tool {name}"}

        # The earlier of the model's `until` and the server's `as_of` wins.
        cutoff = min(filter(None, [day_end(args.get("until")), day_end(as_of)]), default=None)
        hits = self.retriever.search(
            str(args.get("query", "")),
            filters=Filters(
                as_of=cutoff,
                since=args.get("since"),
                company=args.get("company"),
                category=args.get("category"),
            ),
        )
        passages = []
        for hit in hits:
            c = hit.chunk
            source = sources.add(
                c.id,
                title=c.headline,
                text=c.text,
                url=c.url,
                company=c.company,
                published=c.published,
                release_id=c.release_id,
                page=c.page,
            )
            passage = {
                "source": source.id,
                "company": c.company,
                "headline": c.headline,
                "category": c.category,
                "published": c.published,
                "text": c.text,
            }
            passages.append(passage | ({"page": c.page} if c.page else {}))
        if not passages:
            return {"passages": [], "note": "nothing matched"}
        return {"passages": passages}


class PressSpecialist(DocumentSpecialist):
    name = "press"
    prefix = "P"
    summary = (
        "Press releases: events as the companies announced them, such as management changes, "
        "acquisitions, orders, share issues, forecasts and the headline figures of a report."
    )
    documents = "press releases"
    categories = "'Insiderinformation', 'Ändring av styrelse/ledning/revisor'"


class ReportsSpecialist(DocumentSpecialist):
    name = "reports"
    prefix = "R"
    summary = (
        "Full interim, half-year and annual reports (PDF): detailed figures and tables, "
        "segment results, cash flow, balance sheet items, and management's commentary."
    )
    documents = "financial reports"
    categories = "'Interimsrapport', 'Halvårsrapport'"
