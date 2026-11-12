import numpy as np

from disclosures.index import Index, build, chunk_release, chunk_report, split_text
from disclosures.retrieve import Filters

from .conftest import RELEASES, REPORTS, HashEmbedder, release


def chunks(releases):
    return [c for rel in releases for c in chunk_release(rel)]


def test_split_text_respects_the_limit_and_loses_nothing():
    text = "\n".join(["Ett kort stycke."] * 5 + ["ord " * 400])
    chunks = split_text(text, limit=200)
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(chunks).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(
        " ", ""
    )


def test_search_finds_the_matching_release(retriever):
    for mode in ("bm25", "dense", "hybrid"):
        hits = retriever.search("ny verkställande direktör", mode=mode)
        assert hits[0].chunk.release_id == 1, mode


def test_as_of_excludes_later_releases(retriever):
    hits = retriever.search("Alfa sänker prognosen", filters=Filters(as_of="2026-06-30"))
    assert hits and all(h.chunk.published <= "2026-06-30T23:59:59Z" for h in hits)
    assert 3 not in {h.chunk.release_id for h in hits}


def test_as_of_includes_the_whole_day(retriever):
    hits = retriever.search("sänker prognosen", filters=Filters(as_of="2026-09-10"))
    assert 3 in {h.chunk.release_id for h in hits}


def test_filters_by_company_and_category(retriever):
    assert {
        h.chunk.company for h in retriever.search("avtal", filters=Filters(company="beta"))
    } == {"Beta AB"}
    hits = retriever.search("Alfa", filters=Filters(category="insider"))
    assert [h.chunk.release_id for h in hits] == [3]
    assert retriever.search("Alfa", filters=Filters(as_of="2020-01-01")) == []


def test_companies_respects_as_of(retriever):
    assert retriever.companies("", as_of="2026-03-31") == ["Alfa AB"]


def test_rebuild_embeds_only_new_passages(tmp_path):
    class Counting(HashEmbedder):
        seen = 0

        def embed(self, texts, query=False):
            Counting.seen += len(texts)
            return super().embed(texts, query)

    first = build(chunks(RELEASES), Counting())
    first.save(tmp_path)
    before = Counting.seen
    extra = release(
        4, "Delta AB", "Delta får order", "2026-10-01T07:00:00Z", "Delta har fått en order."
    )
    second = build(chunks([*RELEASES, extra]), Counting(), previous=Index.load(tmp_path))
    assert Counting.seen - before == 1
    assert np.allclose(second.vectors[: len(first.vectors)], first.vectors)


def test_report_passages_keep_their_page_and_release():
    first, second = chunk_report(REPORTS[0])
    assert (first.page, second.page) == (1, 2)
    assert first.id != second.id and first.release_id == second.release_id == 10
    assert second.url == "https://example.test/a1.pdf" and "Norden" in second.text


def test_search_reports_by_content(reports_retriever):
    (hit, *_) = reports_retriever.search("rörelseresultat segment Norden")
    assert hit.chunk.page == 2
