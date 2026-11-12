"""The evaluation's own arithmetic: a wrong metric would let a bad deploy through."""

from evals.answers import summarise
from evals.common import Question, load, mentions


def test_mentions_ignores_case_spacing_and_decimal_sign():
    assert mentions("Vd köpte 45 000 aktier.", "45 000")
    assert mentions("Soliditeten var 36.2 procent", "36,2")
    assert mentions("Tina HELLSTADIUS", "Hellstadius")
    assert not mentions("Soliditeten var 36 procent", "36,2")


def test_a_question_without_gold_or_expected_text_is_unanswerable():
    assert not Question("q", "?", "2026-01-01").answerable
    assert Question("q", "?", "2026-01-01", gold=[1]).answerable
    assert Question("q", "?", "2026-01-01", contains=["400"]).answerable


def test_the_question_file_is_well_formed():
    questions = load()
    assert len({q.id for q in questions}) == len(questions)
    for q in questions:
        assert q.split in ("dev", "test") and len(q.as_of) == 10
        assert set(q.specialists) <= {"press", "reports", "insider"}


def answered(**fields):
    base = {
        "gold": [],
        "contains": [],
        "specialists": ["press"],
        "consulted": ["press"],
        "answerable": True,
        "cited": [],
        "leaks": 0,
        "has_expected": True,
        "tool_calls": [],
        "latency_ms": 1000,
        "cost_usd": 0.001,
    }
    return base | fields


def test_summarise():
    records = [
        answered(gold=[1], cited=[1, 2]),  # cites gold and one other release
        answered(gold=[3], cited=[4], consulted=["reports"]),  # wrong specialist, wrong release
        answered(contains=["400"], specialists=["insider"], consulted=["insider"]),
        answered(contains=["400"], has_expected=False, leaks=1),
        answered(answerable=False),  # unanswerable and refused
        answered(),  # unanswerable but answered
    ]
    s = summarise(records)
    assert (s["questions"], s["answered"], s["refused"]) == (6, 1.0, 0.5)
    assert s["routed"] == 0.75  # three of the four answerable questions
    assert (s["cited_gold"], s["citation_precision"]) == (0.5, 0.333)
    assert (s["contains"], s["leaks"]) == (0.5, 1)
