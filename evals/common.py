"""Question set shared by the retrieval and answer evaluations.

evals/questions.jsonl has one question per line:

    id            short identifier
    question      as a user would ask it
    as_of         the date the question is asked on (YYYY-MM-DD), nothing later may be used
    specialists   the specialists a good answer draws on, default ["press"]
    gold          ids of the releases that hold the answer, in the press release itself or
                  in the report attached to it, or empty when no document holds it
    contains      strings a correct answer must include, for example a name or a figure,
                  the only automatic check for insider questions, which have no gold release
    forbidden     strings a correct answer must not include: facts that only became known
                  after `as_of`, for insider questions where no release id can be a trap
    trap          optional release ids published after `as_of` that would answer the
                  question, where returning one of them is a point-in-time leak
    split         "dev" for tuning, "test" for the sealed set that is scored once

A question with neither `gold` nor `contains` cannot be answered from the data as of its
date, and the right response is a refusal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

QUESTIONS = Path(__file__).parent / "questions.jsonl"


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    as_of: str
    gold: list[int] = field(default_factory=list)
    split: str = "dev"
    specialists: list[str] = field(default_factory=lambda: ["press"])
    contains: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    trap: list[int] = field(default_factory=list)
    note: str = ""

    @property
    def answerable(self) -> bool:
        return bool(self.gold or self.contains)


def mentions(answer: str, expected: str) -> bool:
    """Whether `answer` holds `expected`, ignoring case, spacing and the decimal sign.

    "45 000", "45000" and "45\u00a0000" count as the same figure, and "36,2" equals "36.2".
    """

    def plain(text: str) -> str:
        return "".join(text.lower().split()).replace(",", ".")

    return plain(expected) in plain(answer)


def load(split: str | None = None, path: Path = QUESTIONS) -> list[Question]:
    with path.open(encoding="utf-8") as f:
        questions = [Question(**json.loads(line)) for line in f if line.strip()]
    return [q for q in questions if split in (None, q.split)]
