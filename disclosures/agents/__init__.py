"""The multi-agent assistant.

base.py         sources, findings, the tool loop and the citation check
documents.py    press and reports specialists: search an index
insider.py      insider specialist: writes SQL against the trade register
supervisor.py   decides which specialists get the question
judge.py        combines their findings into one answer
graph.py        the LangGraph graph that runs them, and the `Assistant` entry point
"""

from .base import REFUSAL, Finding, Source, Specialist
from .documents import PressSpecialist, ReportsSpecialist
from .graph import Answer, Assistant
from .insider import InsiderSpecialist

__all__ = [
    "REFUSAL",
    "Answer",
    "Assistant",
    "Finding",
    "InsiderSpecialist",
    "PressSpecialist",
    "ReportsSpecialist",
    "Source",
    "Specialist",
]
