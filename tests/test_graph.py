"""The whole assistant: supervisor routing, specialists in parallel, and the judge.

Each specialist gets its own scripted model, because the ones the supervisor chooses run
at the same time. The supervisor and the judge share one, and are called in that order.
"""

import pytest

from disclosures.agents import (
    REFUSAL,
    Assistant,
    InsiderSpecialist,
    PressSpecialist,
    ReportsSpecialist,
)

from .conftest import ScriptedModel, final, plan, search, tool_call

PRESS_ANSWER = [
    search(query="sänker prognosen"),
    final("Alfa sänkte prognosen till 120 MSEK [P1]."),
]
INSIDER_ANSWER = [
    tool_call("query_trades", sql="SELECT person, volume FROM trades WHERE issuer = 'Alfa AB'"),
    final("Anna Berg köpte 2 000 aktier den 15 september [I1]."),
]


@pytest.fixture
def team(retriever, reports_retriever, warehouse):
    """Builds an assistant from scripted replies: team(lead=[...], press=[...], insider=[...])."""

    def make(lead=(), press=(), reports=(), insider=()):
        models = {
            "lead": ScriptedModel(*lead),
            "press": ScriptedModel(*press),
            "reports": ScriptedModel(*reports),
            "insider": ScriptedModel(*insider),
        }
        specialists = [
            PressSpecialist(retriever, models["press"]),
            ReportsSpecialist(reports_retriever, models["reports"]),
            InsiderSpecialist(warehouse, models["insider"]),
        ]
        return Assistant(specialists, models["lead"]), models

    return make


def test_only_the_chosen_specialist_runs_and_its_finding_is_the_answer(team):
    assistant, models = team(lead=[plan(press="Har Alfa sänkt prognosen?")], press=PRESS_ANSWER)
    ans = assistant.answer("Har Alfa sänkt sin prognos?")
    assert ans.answerable and ans.text == "Alfa sänkte prognosen till 120 MSEK [P1]."
    assert [f.specialist for f in ans.findings] == ["press"]
    assert models["reports"].sent == [] and models["insider"].sent == []
    assert len(models["lead"].sent) == 1  # one specialist asked: nothing for the judge to combine
    assert (ans.tokens_in, ans.tokens_out) == (50 + 400, 5 + 50)


def test_each_specialist_gets_its_own_question(team):
    assistant, models = team(
        lead=[plan(press="Fråga till press", insider="Fråga till insyn"), final("NO_ANSWER")],
        press=PRESS_ANSWER,
        insider=INSIDER_ANSWER,
    )
    assistant.answer("Ursprunglig fråga")
    assert models["press"].sent[0][1].content == "Fråga till press"
    assert models["insider"].sent[0][1].content == "Fråga till insyn"


def test_judge_combines_findings_and_keeps_their_sources(team):
    combined = "Alfa sänkte prognosen [P1]. Därefter köpte vd Anna Berg 2 000 aktier [I1]."
    assistant, models = team(
        lead=[
            plan(press="Har Alfa sänkt prognosen?", insider="Har ledningen i Alfa köpt?"),
            final(combined),
        ],
        press=PRESS_ANSWER,
        insider=INSIDER_ANSWER,
    )
    ans = assistant.answer("Sänkte Alfa prognosen, och handlade ledningen efteråt?")
    assert ans.answerable and ans.text == combined
    assert [(s.id, s.kind) for s in ans.citations] == [("P1", "press"), ("I1", "insider")]
    brief = models["lead"].sent[1][1].content  # what the judge was shown
    assert "[P1] Alfa AB, Alfa sänker prognosen, 2026-09-10" in brief and "[I1]" in brief
    assert ans.tokens_in == 50 + 400 + 400 + 300


def test_judge_answer_must_cite_what_the_specialists_cited(team):
    for text in ("Alfa sänkte prognosen och vd köpte aktier.", "Alfa sänkte prognosen [R7]."):
        assistant, _ = team(
            lead=[plan(press="a?", insider="b?"), final(text)],
            press=PRESS_ANSWER,
            insider=INSIDER_ANSWER,
        )
        ans = assistant.answer("Fråga?")
        assert not ans.answerable and ans.text == REFUSAL and ans.citations == []


def test_judge_is_told_which_specialist_found_nothing(team):
    combined = "Anna Berg köpte 2 000 aktier [I1]. Källorna säger inget om utdelningen."
    assistant, models = team(
        lead=[plan(press="a?", insider="b?"), final(combined)],
        press=[search(query="utdelning"), final("NO_ANSWER\nInget om utdelning.")],
        insider=INSIDER_ANSWER,
    )
    ans = assistant.answer("Fråga?")
    assert ans.answerable and ans.text == combined and [s.id for s in ans.citations] == ["I1"]
    brief = models["lead"].sent[1][1].content
    assert "No finding from the press specialist." in brief
    assert "Inget om utdelning" not in brief  # uncited text never reaches the judge


def test_refusal_when_no_specialist_can_answer(team):
    refuse = [search(query="x"), final("NO_ANSWER\nSaknas.")]
    assistant, _ = team(lead=[plan(press="a?", reports="b?")], press=refuse, reports=refuse)
    ans = assistant.answer("Fråga?")
    assert not ans.answerable and ans.text == REFUSAL


def test_a_broken_plan_falls_back_to_asking_everyone(team):
    refuse = [final("NO_ANSWER")]
    for broken in (plan(), plan(weather="Blir det regn?"), None):
        assistant, models = team(lead=[broken], press=refuse, reports=refuse, insider=refuse)
        ans = assistant.answer("Ursprunglig fråga")
        assert sorted(f.specialist for f in ans.findings) == ["insider", "press", "reports"]
        assert models["insider"].sent[0][1].content == "Ursprunglig fråga"


def test_as_of_reaches_every_specialist(team):
    assistant, models = team(
        lead=[plan(press="Har Alfa sänkt prognosen?", insider="Hur många affärer?")],
        press=[search(query="Alfa sänker prognosen"), final("NO_ANSWER")],
        insider=[
            tool_call("query_trades", sql="SELECT COUNT(*) AS n FROM trades"),
            final("NO_ANSWER"),
        ],
    )
    assistant.answer("Fråga?", as_of="2026-06-30")
    assert "2026-09-10" not in models["press"].sent[1][-1].content
    assert '"n": 1' in models["insider"].sent[1][-1].content


def test_a_single_specialist_needs_no_supervisor_and_no_judge(retriever):
    lead = ScriptedModel()
    assistant = Assistant([PressSpecialist(retriever, ScriptedModel(*PRESS_ANSWER))], lead)
    ans = assistant.answer("Har Alfa sänkt prognosen?")
    assert ans.answerable and lead.sent == []
