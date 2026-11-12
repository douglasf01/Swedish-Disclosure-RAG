"""The judge: turns the specialists' findings into one answer.

It sees each finding with the sources that finding cites, and nothing else. Its text goes
through the same citation check as a specialist's, against the sources they cited.

The model is only called when there is something to combine:

    no finding answered               refusal
    one specialist asked, answered    that finding is the answer
    several specialists asked         the model combines what they found
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from .base import NO_ANSWER, REFUSAL, Finding, Source, Usage, check_citations

PROMPT = """You are the editor of an analyst assistant for companies listed on Nasdaq \
Stockholm. Today's date is {today}. Specialists have each answered part of a user's \
question from their own source. Write the one answer the user will read.

Rules:
- Use only what the findings and their sources say. Add nothing from your own knowledge.
- Keep the source ids exactly as given, in square brackets after each claim, for example \
[P1] or [R2][I1]. Never invent an id.
- Check each claim you take from a finding against the source text it cites. Leave out a \
claim its source does not support.
- If two findings disagree on a fact, say so plainly and cite both sides.
- A specialist that found nothing says so under "No finding". If that leaves part of the \
question open, tell the user which part the sources did not cover. That sentence needs no \
source id.
- If the findings together do not answer any part of the question: reply with {no_answer} \
on the first line, then one sentence on what is missing.
- Answer briefly, in the language of the question."""


@dataclass
class Verdict:
    text: str
    answerable: bool
    citations: list[Source] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


def _brief(finding: Finding) -> str:
    """One finding as the judge reads it: the specialist's text, then the sources it cites."""
    if not finding.answerable:
        # Its explanation is uncited text, so the judge must not see figures from it.
        return f"No finding from the {finding.specialist} specialist."
    lines = [f"Finding from the {finding.specialist} specialist:", finding.text, "", "Sources:"]
    for s in finding.sources:
        about = ", ".join(filter(None, [s.company, s.title, s.published[:10]]))
        lines.append(f"[{s.id}] {about}\n{s.text}")
    return "\n".join(lines)


class Judge:
    def __init__(self, model: BaseChatModel) -> None:
        self.model = model

    def rule(self, question: str, today: str, findings: list[Finding]) -> Verdict:
        answered = [f for f in findings if f.answerable]
        if not answered:
            # A single specialist's own explanation of what is missing is worth passing on.
            return Verdict(findings[0].text if len(findings) == 1 else REFUSAL, answerable=False)
        if len(findings) == 1:
            return Verdict(answered[0].text, answerable=True, citations=answered[0].sources)

        briefs = "\n\n---\n\n".join(_brief(f) for f in findings)
        reply = self.model.invoke(
            [
                SystemMessage(PROMPT.format(today=today, no_answer=NO_ANSWER)),
                HumanMessage(f"Question: {question}\n\n{briefs}"),
            ]
        )
        verdict = Verdict("", answerable=False)
        verdict.usage.count(reply)
        cited = [s for f in answered for s in f.sources]
        verdict.text, verdict.answerable, verdict.citations = check_citations(
            str(reply.text), cited
        )
        return verdict
