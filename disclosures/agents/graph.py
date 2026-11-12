"""The assistant: a LangGraph graph that wires supervisor, specialists and judge together.

                      ┌──> press ────┐
    supervisor ───────┼──> reports ──┼──> judge
    (who gets what)   └──> insider ──┘    (one answer)

The supervisor's plan decides which specialist nodes run. The ones it chooses run in
parallel, each with its own question. Their findings are collected in the state, and the
judge runs once when all of them are done.

The graph holds no logic of its own. Each node calls one object from this package and
writes its result to the state.
"""

from __future__ import annotations

import operator
import time
from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .base import Finding, Source, Specialist, Usage, today
from .judge import Judge, Verdict
from .supervisor import Assignment, Supervisor


class State(TypedDict, total=False):
    question: str
    as_of: str | None
    assignments: list[Assignment]
    # Written by several nodes in the same step, so new values are appended, not assigned.
    findings: Annotated[list[Finding], operator.add]
    usage: Annotated[list[Usage], operator.add]
    verdict: Verdict


class Task(TypedDict):
    """What one specialist node receives from the supervisor."""

    question: str
    as_of: str | None


@dataclass
class Answer:
    text: str
    answerable: bool
    citations: list[Source] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)  # one per specialist consulted
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


class Assistant:
    """Answers a question by consulting the given specialists.

    With one specialist the graph degenerates to that specialist alone: the supervisor
    has nothing to route and the judge nothing to combine, so neither calls the model.
    """

    def __init__(self, specialists: list[Specialist], model: BaseChatModel) -> None:
        self.specialists = {s.name: s for s in specialists}
        self.supervisor = Supervisor(model, self.specialists)
        self.judge = Judge(model)
        self.graph = self._build()

    def _build(self):
        graph = StateGraph(State)
        graph.add_node("supervisor", self._plan)
        graph.add_node("judge", self._rule)
        for name, specialist in self.specialists.items():
            graph.add_node(name, self._consult(specialist))
            graph.add_edge(name, "judge")
        graph.add_edge(START, "supervisor")
        graph.add_conditional_edges("supervisor", self._dispatch, list(self.specialists))
        graph.add_edge("judge", END)
        return graph.compile()

    def _plan(self, state: State) -> dict:
        assignments, usage = self.supervisor.plan(state["question"], today(state["as_of"]))
        return {"assignments": assignments, "usage": [usage]}

    @staticmethod
    def _dispatch(state: State) -> list[Send]:
        """One parallel branch per assignment, each to the node of its specialist."""
        return [
            Send(a.specialist, Task(question=a.question, as_of=state["as_of"]))
            for a in state["assignments"]
        ]

    @staticmethod
    def _consult(specialist: Specialist):
        def node(task: Task) -> dict:
            finding = specialist.run(task["question"], task["as_of"])
            return {"findings": [finding], "usage": [finding.usage]}

        return node

    def _rule(self, state: State) -> dict:
        verdict = self.judge.rule(state["question"], today(state["as_of"]), state["findings"])
        return {"verdict": verdict, "usage": [verdict.usage]}

    def answer(self, question: str, as_of: str | None = None) -> Answer:
        started = time.monotonic()
        state = self.graph.invoke({"question": question, "as_of": as_of})
        verdict: Verdict = state["verdict"]
        return Answer(
            text=verdict.text,
            answerable=verdict.answerable,
            citations=verdict.citations,
            findings=state["findings"],
            tokens_in=sum(u.tokens_in for u in state["usage"]),
            tokens_out=sum(u.tokens_out for u in state["usage"]),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
