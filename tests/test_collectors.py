"""Collector and refresh regressions.

These cover real failure modes observed against live Gumtree pages: promoted
cards that are absent from JSON-LD, generic titles that can match several URLs,
and valuation refreshes that were skipped after another source had already
refreshed a few seconds earlier.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.collectors.gumtree import GumtreeCollector
from app.db import Database
from app.scanner import Scanner
from app.utils import iso

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "collectors.db")
    try:
        yield database
    finally:
        database.close()


def test_gumtree_uses_jsonld_and_ignores_promoted_cards(db):
    collector = GumtreeCollector(db)
    html = (FIXTURES / "gumtree_search.html").read_text(encoding="utf-8")

    items = collector.parse_search(html, "rog ally")

    assert len(items) == 2
    by_id = {item.external_id: item for item in items}
    ally_x = by_id["1802400763"]
    assert ally_x.title == "Asus ROG Ally X Handheld Gaming PC - As Good As New"
    assert ally_x.price == 390
    assert "barely used" in ally_x.description
    assert ally_x.condition_raw == "Used"
    assert ally_x.raw["source"] == "jsonld"

    plain = by_id["1801525250"]
    assert plain.title == "ROG Ally Handheld Gaming Console"
    assert plain.price == 150
    # The promoted £829.99 card is not in the ItemList and must not borrow
    # either canonical URL.
    assert all(item.price != 829.99 for item in items)


def test_gumtree_empty_search_is_not_a_parser_failure(db):
    collector = GumtreeCollector(db)
    html = "<html><body><main><h1>Home For Sale</h1><p>0 ads for intel arc gpu in All Classifieds</p></main></body></html>"
    assert collector.parse_search(html, "intel arc gpu") == []
    assert collector._is_empty_page(html) is True


def test_gumtree_rejects_ambiguous_card_url_pairing(db):
    collector = GumtreeCollector(db)
    urls = [
        "https://www.gumtree.com/p/other-games-consoles/rog-ally-handheld-gaming-console/1801525250",
        "https://www.gumtree.com/p/other-games-consoles/asus-rog-ally-x-handheld-gaming-pc-as-good-as-new/1802400763",
    ]
    # Two generic titles must not be force-matched to a URL whose slug carries
    # the identity of a different model.
    assert collector._strict_match_url("Gaming Console", urls) is None
    assert collector._strict_match_url("ROG Ally Handheld Gaming Console", urls).endswith("1801525250")
    assert (
        collector._strict_match_url("Asus ROG Ally X Handheld Gaming PC As Good As New", urls)
        .endswith("1802400763")
    )


def test_scanner_refresh_runs_after_ingest_even_if_recent(db, monkeypatch):
    calls: list[str] = []

    class FakeValuation:
        def refresh(self, limit: int = 100000):
            calls.append("refresh")
            return {"evaluated": 0, "deals": 0, "review": 0}

    scanner = Scanner(db)
    scanner.valuation = FakeValuation()
    try:
        scanner._valued_version = scanner._data_version
        asyncio.run(scanner._run_refresh())
        assert calls == [], "an unchanged dataset should not be revalued"

        scanner._data_version += 1
        asyncio.run(scanner._run_refresh())
        assert calls == ["refresh"]
        assert scanner._valued_version == scanner._data_version

        # A second scan with no new ingest is a no-op.
        asyncio.run(scanner._run_refresh())
        assert calls == ["refresh"]
    finally:
        asyncio.run(scanner.cex.close())
        asyncio.run(scanner.notifier.close())
        for collector in scanner.collectors.values():
            asyncio.run(collector.close())
