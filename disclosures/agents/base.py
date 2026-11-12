"""What every specialist shares: sources, findings, the tool loop and the citation check.

A specialist is a model with tools over one data source. It answers a question with a
`Finding`: a short text in which every claim is followed by source ids such as [P2].

Two rules are enforced in code, not left to the prompt:

* Point in time. The `as_of` date is applied by the server inside every tool call. The
  model cannot widen it, so it never sees anything published after that date.
* Grounded or refuse. Everything a tool shows the model is registered as a numbered source.
  A text that cites no registered source is replaced by a refusal.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

MAX_STEPS = 5  # rounds of tool calls before a specialist has to answer
NO_ANSWER = "NO_ANSWER"
REFUSAL = "I could not find support for an answer in the sources."
CITATION = re.compile(r"\[([A-Z]\d+(?:\s*,\s*[A-Z]\d+)*)\]")  # [P1] or [P1, R3]


@dataclass(frozen=True)
class Source:
    """One thing a specialist showed the model: a passage, or the result of a query."""

    id: str  # the specialist's letter and a running number, e.g. "P2"
    kind: str  # name of the specialist it came from
    title: str
    text: str
    url: str
    company: str = ""
    published: str = ""  # ISO 8601, UTC, or empty when the source is not a dated document
    release_id: int | None = None
    page: int | None = None


class Sources:
    """The sources one specialist has shown its model during one question."""

    def __init__(self, kind: str, prefix: str) -> None:
        self.kind = kind
        self.prefix = prefix
        self.by_key: dict[str, Source] = {}

    def add(self, key: str, **fields) -> Source:
        """Register a source, or return the one already registered under `key`."""
        if key not in self.by_key:
            source_id = f"{self.prefix}{len(self.by_key) + 1}"
            self.by_key[key] = Source(id=source_id, kind=self.kind, **fields)
        return self.by_key[key]

    def all(self) -> list[Source]:
        return list(self.by_key.values())


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0

    def count(self, message: AIMessage) -> None:
        usage = message.usage_metadata or {}
        self.tokens_in += usage.get("input_tokens", 0)
        self.tokens_out += usage.get("output_tokens", 0)


@dataclass
class Finding:
    """A specialist's answer to the question it was given."""

    specialist: str
    question: str
    text: str
    answerable: bool
    sources: list[Source] = field(default_factory=list)  # the sources the text cites
    tool_calls: list[dict] = field(default_factory=list)  # {"name": ..., "args": ...}
    usage: Usage = field(default_factory=Usage)
    stopped_early: bool = False  # ran out of steps while still calling tools


def tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    """A tool definition in the function-calling format every chat model integration accepts."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def today(as_of: str | None) -> str:
    """The date a question is answered on: `as_of` if given, otherwise the current date."""
    return (as_of or datetime.now(UTC).strftime("%Y-%m-%d"))[:10]


def day_end(day: str | None) -> str | None:
    """'2026-11-05' -> '2026-11-05T23:59:59Z'. Full timestamps and None pass through."""
    if not day:
        return None
    return day + "T23:59:59Z" if len(day) == 10 else day


def check_citations(text: str, sources: list[Source]) -> tuple[str, bool, list[Source]]:
    """Apply the grounded-or-refuse rule. Returns the text, whether it answers, and what it cites.

    A text starting with NO_ANSWER is the model's own refusal. The rest of it says what is
    missing and is passed on, without source ids. Any other text must cite at least one of
    `sources`.
    """
    text = text.strip()
    if not text or text.startswith(NO_ANSWER):
        reason = re.sub(rf"\s*{CITATION.pattern}", "", text.removeprefix(NO_ANSWER)).strip(" :\n")
        return reason or REFUSAL, False, []
    by_id = {s.id: s for s in sources}
    cited = dict.fromkeys(
        i for group in CITATION.findall(text) for i in re.split(r"\s*,\s*", group)
    )
    valid = [by_id[i] for i in cited if i in by_id]
    if not valid:
        return REFUSAL, False, []
    return text, True, valid


class Specialist:
    """A model with tools over one data source. Subclasses supply the prompt and the tools."""

    name: str  # how the supervisor refers to it, e.g. "press"
    prefix: str  # letter of its source ids, e.g. "P"
    summary: str  # one sentence for the supervisor: what this specialist can answer
    tools: list[dict]

    def __init__(self, model: BaseChatModel) -> None:
        self.model = model.bind_tools(self.tools)

    def system_prompt(self, today: str) -> str:
        raise NotImplementedError

    def call(self, name: str, args: dict, as_of: str | None, sources: Sources) -> dict:
        """Run one tool call. Whatever it returns to the model must be registered in `sources`."""
        raise NotImplementedError

    def run(self, question: str, as_of: str | None = None) -> Finding:
        sources = Sources(self.name, self.prefix)
        finding = Finding(self.name, question, text="", answerable=False)
        messages = [SystemMessage(self.system_prompt(today(as_of))), HumanMessage(question)]

        reply = self.model.invoke(messages)
        for _ in range(MAX_STEPS):
            finding.usage.count(reply)
            if not reply.tool_calls:
                break
            messages.append(reply)
            for call in reply.tool_calls:
                result = self.call(call["name"], call["args"], as_of, sources)
                content = json.dumps(result, ensure_ascii=False)
                messages.append(ToolMessage(content, tool_call_id=call["id"]))
                finding.tool_calls.append({"name": call["name"], "args": call["args"]})
            reply = self.model.invoke(messages)
        else:
            finding.usage.count(reply)
            finding.stopped_early = bool(reply.tool_calls)

        finding.text, finding.answerable, finding.sources = check_citations(
            str(reply.text), sources.all()
        )
        return finding
