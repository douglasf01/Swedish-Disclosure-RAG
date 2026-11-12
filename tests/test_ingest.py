from datetime import date

from disclosures import store
from disclosures.ingest import insider, press, reports
from disclosures.store import Release, Report

from .conftest import TRADES

PAGE = """<html><body><h1 id="view-headline">Rubrik</h1><nav>Dela</nav>
<div id="view-body"><html><body><div><p>F&ouml;rsta stycket&nbsp;h&auml;r.</p>
<style>p{color:red}</style><p>Andra <strong>stycket</strong>.</p>
<table><tr><td>Oms&auml;ttning</td><td>120</td></tr></table></div></body></html></div>
<footer>Sidfot</footer></body></html>"""


def listing(category, attachments=()):
    item = {
        "disclosureId": 7,
        "company": " Alfa AB ",
        "headline": "Rubrik",
        "cnsCategory": category,
        "language": "sv",
        "published": "2026-11-11 18:25:47 +0100",
        "messageUrl": "https://x.test/7",
        "attachment": list(attachments),
    }
    return {"results": {"item": [item]}}


PDF = {"mimetype": "application/pdf", "fileName": "q3.pdf", "attachmentUrl": "https://x.test/abc"}
IMAGE = {"mimetype": "image/png", "fileName": "vd.png", "attachmentUrl": "https://x.test/img"}


def test_body_text_keeps_only_the_release_body():
    assert press.body_text(PAGE) == "Första stycket här.\nAndra stycket.\nOmsättning 120"


def test_parse_listing_converts_time_to_utc():
    (rel,) = press.parse_listing(listing("Insiderinformation"))
    assert (rel.id, rel.company, rel.published) == (7, "Alfa AB", "2026-11-11T17:25:47Z")


def test_store_ignores_releases_it_already_has(tmp_path):
    con = store.connect(tmp_path / "r.db")
    rel = Release(
        1, "Alfa AB", "Rubrik", "Kategori", "sv", "2026-01-01T00:00:00Z", "https://x.test/1"
    )
    assert store.add_releases(con, [rel]) == 1
    store.set_release_text(con, 1, "text")
    assert store.add_releases(con, [rel]) == 0
    assert store.releases_without_text(con) == []
    assert store.releases_with_text(con)[0].text == "text"


def test_reports_are_the_pdfs_of_report_releases():
    (rep,) = reports.parse_listing(listing("Interimsrapport", [PDF, IMAGE]))
    assert (rep.id, rep.release_id, rep.company, rep.url) == (
        "abc",
        7,
        "Alfa AB",
        "https://x.test/abc",
    )
    assert reports.parse_listing(listing("Nyheter för investerare", [PDF])) == []


def test_store_keeps_report_text_and_skips_unreadable_reports(tmp_path):
    con = store.connect(tmp_path / "r.db")
    base = ("Alfa AB", "Rubrik", "Interimsrapport", "2026-01-01T00:00:00Z")
    good = Report("a", 1, *base, "https://x.test/a", "a.pdf")
    bad = Report("b", 1, *base, "https://x.test/b", "b.pdf")
    assert store.add_reports(con, [good, bad]) == 2 and store.add_reports(con, [good]) == 0
    store.set_report_text(con, "a", "sida 1\fsida 2")
    store.set_report_text(con, "b", "")  # could not be read: stored, but never indexed
    assert store.reports_without_text(con) == []
    assert [r.id for r in store.reports_with_text(con)] == ["a"]


EXPORT = (
    "Publiceringsdatum;Emittent;LEI-kod;Anmälningsskyldig;Person i ledande ställning;Befattning;"
    "Närstående;Korrigering;Beskrivning av korrigering;Är förstagångsrapportering;"
    "Är kopplad till aktieprogram;Karaktär;Instrumenttyp;Instrumentnamn;ISIN;Transaktionsdatum;"
    "Volym;Volymsenhet;Pris;Valuta;Handelsplats;Status;\r\n"
    "2026-11-10 08:32:37;Mangold AB;549300F7;Per Bolag AB;Per Ek;Verkställande\xa0direktör\xa0(VD);"
    "Ja;;;Ja;;Förvärv;Aktie;Mangold AB;SE0001449380;2026-11-09 00:00:00;"
    "1 050,0;Antal;1229,50;SEK;NASDAQ STOCKHOLM AB;Aktuell;\r\n"
).encode("utf-16")


def test_parse_export_names_types_and_utc_time():
    (trade,) = insider.parse_export(EXPORT)
    assert trade["published"] == "2026-11-10T07:32:37Z"  # 08:32 Swedish winter time
    assert (trade["issuer"], trade["person"], trade["notifier"]) == (
        "Mangold AB",
        "Per Ek",
        "Per Bolag AB",
    )
    assert trade["position"] == "Verkställande direktör (VD)"  # non-breaking spaces removed
    assert (trade["closely_associated"], trade["correction"]) == (1, 0)
    assert (trade["volume"], trade["price"]) == (1050.0, 1229.5)
    assert trade["transaction_date"] == "2026-11-09"
    assert set(trade) == {c.name for c in store.TRADE_COLUMNS}


def test_fetch_splits_a_window_that_hits_the_row_limit(tmp_path, monkeypatch):
    asked = []

    def export(first, last):
        asked.append((first.day, last.day))
        full = (last - first).days >= 3  # pretend any window of four days or more is cut off
        return TRADES[:1] * (insider.EXPORT_LIMIT if full else 1)

    monkeypatch.setattr(insider, "export", export)
    con = store.connect(tmp_path / "r.db")
    insider.fetch(con, date(2026, 3, 1), date(2026, 3, 7), delay=0)
    assert asked == [(1, 7), (1, 3), (4, 7), (4, 4), (5, 7)]


def test_fetching_the_same_days_again_replaces_them(tmp_path, monkeypatch):
    monkeypatch.setattr(insider, "export", lambda first, last: TRADES[:1])  # published 5 March
    con = store.connect(tmp_path / "r.db")
    for _ in range(2):
        insider.fetch(con, date(2026, 3, 1), date(2026, 3, 7), delay=0)
    assert len(store.trades(con)) == 1
    assert store.latest_trade(con) == "2026-03-05T09:00:00Z"
