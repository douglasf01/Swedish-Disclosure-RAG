from fastapi.testclient import TestClient

from disclosures.agents import Assistant, PressSpecialist
from disclosures.app import create_app
from disclosures.config import Settings

from .conftest import ScriptedModel, final, search


def client(retriever, replies, **env):
    assistant = Assistant([PressSpecialist(retriever, ScriptedModel(*replies))], ScriptedModel())
    return TestClient(create_app(assistant, {"press": retriever}, Settings(**env)))


def test_ask_returns_answer_citations_and_cost(retriever):
    replies = [search(query="förvärv Gamma"), final("Beta förvärvar Gamma för 400 MSEK [P1].")]
    with client(retriever, replies) as c:
        r = c.post("/ask", json={"question": "Vad köper Beta?"})
    body = r.json()
    assert r.status_code == 200 and body["answerable"]
    cited = body["citations"][0]
    assert (cited["id"], cited["kind"], cited["company"]) == ("P1", "press", "Beta AB")
    assert body["tokens"] == {"in": 400, "out": 50} and body["cost_usd"] > 0
    (asked,) = body["specialists"]
    assert asked["name"] == "press" and asked["answerable"]
    assert asked["tool_calls"] == [{"name": "search", "args": {"query": "förvärv Gamma"}}]


def test_ask_validates_input(retriever):
    with client(retriever, []) as c:
        assert c.post("/ask", json={"question": "x"}).status_code == 422
        assert c.post("/ask", json={"question": "Vad hände?", "as_of": "igår"}).status_code == 422


def test_model_failure_becomes_a_clean_error(retriever):
    with client(retriever, []) as c:  # the scripted model has no reply left and raises
        assert c.post("/ask", json={"question": "Vad hände?"}).status_code == 502


def test_rate_limit_and_daily_cap(retriever):
    replies = [final("NO_ANSWER")] * 5
    with client(retriever, replies, per_ip_per_min=2, daily_cap=10) as c:
        codes = [c.post("/ask", json={"question": "Vad hände?"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    with client(retriever, replies, per_ip_per_min=10, daily_cap=1) as c:
        codes = [c.post("/ask", json={"question": "Vad hände?"}).status_code for _ in range(2)]
    assert codes == [200, 429]


def test_search_and_health_need_no_model(retriever):
    with client(retriever, []) as c:
        hits = c.get("/search", params={"q": "förvärva aktier", "as_of": "2026-12-31"}).json()[
            "hits"
        ]
        assert hits[0]["release_id"] == 2
        assert c.get("/search", params={"q": "x", "corpus": "reports"}).status_code == 404
        assert c.get("/health").json()["corpora"] == {
            "press": {"passages": 3, "newest": "2026-09-10T16:00:00Z"}
        }
        assert "Swedish Disclosure RAG" in c.get("/").text
