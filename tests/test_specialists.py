"""Each specialist on its own: the tool loop, the two enforced rules, and its tools."""

from disclosures.agents import REFUSAL, InsiderSpecialist, PressSpecialist, ReportsSpecialist
from disclosures.agents.base import MAX_STEPS

from .conftest import ScriptedModel, final, search, tool_call, tool_result


def test_answer_with_citation(retriever):
    model = ScriptedModel(
        search(query="ny verkställande direktör"), final("Alfa har utsett Anna Berg till vd [P1].")
    )
    finding = PressSpecialist(retriever, model).run("Vem är ny vd i Alfa?")
    assert finding.answerable and finding.specialist == "press"
    assert [(s.id, s.kind, s.release_id) for s in finding.sources] == [("P1", "press", 1)]
    assert (finding.usage.tokens_in, finding.usage.tokens_out) == (400, 50)
    passage = tool_result(model)["passages"][0]
    assert passage["source"] == "P1" and passage["company"] == "Alfa AB"


def test_answer_without_valid_citation_becomes_a_refusal(retriever):
    for text in ("Alfa har bytt vd.", "Alfa har bytt vd [P99].", "Alfa har bytt vd [R1]."):
        model = ScriptedModel(search(query="vd"), final(text))
        finding = PressSpecialist(retriever, model).run("Vem är ny vd i Alfa?")
        assert not finding.answerable and finding.text == REFUSAL and finding.sources == []


def test_several_ids_in_one_bracket_are_all_counted(retriever):
    model = ScriptedModel(
        search(query="Alfa"), final("Alfa bytte vd och sänkte prognosen [P1, P2].")
    )
    finding = PressSpecialist(retriever, model).run("Vad har hänt i Alfa?")
    assert [s.id for s in finding.sources] == ["P1", "P2"]


def test_model_refusal_is_passed_on(retriever):
    model = ScriptedModel(
        search(query="utdelning"), final("NO_ANSWER\nInget meddelande nämner utdelningen [P1].")
    )
    finding = PressSpecialist(retriever, model).run("Vad blir utdelningen i Beta?")
    assert not finding.answerable and finding.sources == []
    assert finding.text == "Inget meddelande nämner utdelningen."


def test_model_cannot_search_past_as_of(retriever):
    # The model asks for releases up to year end. The server's as_of must win.
    model = ScriptedModel(
        search(query="Alfa sänker prognosen", until="2026-12-31"), final("NO_ANSWER")
    )
    PressSpecialist(retriever, model).run("Har Alfa sänkt prognosen?", as_of="2026-06-30")
    passages = tool_result(model)["passages"]
    assert passages and all(p["published"] <= "2026-06-30T23:59:59Z" for p in passages)
    assert "2026-06-30" in model.sent[0][0].content  # the prompt states the date


def test_model_until_narrows_the_search(retriever):
    model = ScriptedModel(search(query="Alfa", until="2026-03-31"), final("NO_ANSWER"))
    PressSpecialist(retriever, model).run("Vad hände i Alfa under första kvartalet?")
    assert {p["published"][:7] for p in tool_result(model)["passages"]} == {"2026-03"}


def test_stops_after_max_steps(retriever):
    model = ScriptedModel(*[search(query="Alfa")] * (MAX_STEPS + 1))
    finding = PressSpecialist(retriever, model).run("Fråga som aldrig blir klar?")
    assert finding.stopped_early and not finding.answerable
    assert len(finding.tool_calls) == MAX_STEPS


def test_reports_specialist_cites_the_page(reports_retriever):
    model = ScriptedModel(
        search(query="rörelseresultat segment Norden"),
        final("Rörelseresultatet i Norden var 75 MSEK [R1]."),
    )
    finding = ReportsSpecialist(reports_retriever, model).run("Vad tjänade Alfa i Norden?")
    assert finding.answerable
    assert (finding.sources[0].id, finding.sources[0].page) == ("R1", 2)
    assert tool_result(model)["passages"][0]["page"] == 2


def test_insider_specialist_cites_a_query_result(warehouse):
    sql = "SELECT SUM(volume) AS shares FROM trades WHERE issuer = 'Alfa AB'"
    model = ScriptedModel(
        tool_call("query_trades", sql=sql), final("Anna Berg köpte 3 000 aktier [I1].")
    )
    finding = InsiderSpecialist(warehouse, model).run("Hur många aktier har köpts i Alfa?")
    assert finding.answerable and tool_result(model) == {
        "source": "I1",
        "rows": [{"shares": 3000.0}],
    }
    assert finding.sources[0].kind == "insider" and sql in finding.sources[0].text
    assert "SQLite" in model.sent[0][0].content  # the prompt names the dialect


def test_insider_specialist_cannot_see_trades_after_as_of(warehouse):
    sql = "SELECT COUNT(*) AS n FROM trades"
    model = ScriptedModel(tool_call("query_trades", sql=sql), final("NO_ANSWER"))
    InsiderSpecialist(warehouse, model).run("Hur många affärer?", as_of="2026-06-30")
    assert tool_result(model)["rows"] == [{"n": 1}]


def test_insider_specialist_gets_sql_errors_back(warehouse):
    model = ScriptedModel(
        tool_call("query_trades", sql="DELETE FROM trades"),
        tool_call("query_trades", sql="SELECT nope FROM trades"),
        final("NO_ANSWER"),
    )
    finding = InsiderSpecialist(warehouse, model).run("Radera allt")
    assert "SELECT" in tool_result(model, call=1)["error"]
    assert "nope" in tool_result(model, call=2)["error"]
    assert not finding.answerable
