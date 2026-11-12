"""The supervisor: decides which specialists get the question, and in what form.

It never answers anything itself. Its whole output is a plan, a list of assignments, and
the code below repairs a plan that names an unknown specialist or names nobody.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .base import Specialist, Usage

PROMPT = """You route questions about companies listed on Nasdaq Stockholm to specialists. \
Today's date is {today}.

The specialists:
{specialists}

Decide which of them are needed to answer the question, and give each one its own question.

Rules:
- Choose every specialist whose source is likely to hold part of the answer, and no others. \
Most questions need one specialist. A question that combines kinds of information needs several.
- Each specialist sees only its own question, so make it self-contained: name the company \
and the period, and ask only for what that specialist's source can hold.
- The specialists work at the same time and cannot see each other's answers. When one part \
of the question depends on another ("after the announcement", "since the report"), do not \
pass the dependency on. Ask for the wider facts with their dates, for example every trade in \
the company during the months concerned, so that the two answers can be related afterwards.
- Keep the language of the original question.
- Assign at most one question to each specialist."""


class Assignment(BaseModel):
    specialist: str = Field(description="Name of the specialist")
    question: str = Field(description="The question for this specialist, self-contained")


class Plan(BaseModel):
    assignments: list[Assignment]


class Supervisor:
    def __init__(self, model: BaseChatModel, specialists: dict[str, Specialist]) -> None:
        self.specialists = specialists
        self.model = model.with_structured_output(Plan, include_raw=True)

    def plan(self, question: str, today: str) -> tuple[list[Assignment], Usage]:
        """Who should answer what. With a single specialist there is nothing to decide."""
        usage = Usage()
        everyone = [Assignment(specialist=name, question=question) for name in self.specialists]
        if len(self.specialists) == 1:
            return everyone, usage

        listing = "\n".join(f"- {s.name}: {s.summary}" for s in self.specialists.values())
        reply = self.model.invoke(
            [
                SystemMessage(PROMPT.format(today=today, specialists=listing)),
                HumanMessage(question),
            ]
        )
        usage.count(reply["raw"])
        plan: Plan | None = reply["parsed"]

        # Keep the first assignment per known specialist. If nothing usable is left, a
        # broken plan must not become a refusal, so everyone gets the original question.
        chosen: dict[str, Assignment] = {}
        for assignment in plan.assignments if plan else []:
            if assignment.specialist in self.specialists and assignment.question.strip():
                chosen.setdefault(assignment.specialist, assignment)
        return list(chosen.values()) or everyone, usage
