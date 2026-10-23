"""Collector configuration, eBay parser edges and scan instrumentation."""
from __future__ import annotations

import asyncio

from app.collectors.base import BaseCollector, RawListing, SearchOutcome, run_queries
from app.collectors.ebay import EbayCollector
from app.db import Database
from app.scan_config import save_scan_config
from app.scan_taxonomy import all_leaf_keys
from app.scanner import Scanner
from app.utils import iso


def _ebay_item(**overrides):
    base = {
        "title": "NVIDIA RTX 4070 Founders Edition",
        "itemId": "v1|123|0",
        "itemWebUrl": "https://www.ebay.co.uk/itm/123?campid=1",
        "price": {"value": "450.00", "currency": "GBP"},
        "shippingOptions": [{"shippingCost": {"value": "5.00", "currency": "GBP"}}],
        "condition": "Used",
        "buyingOptions": ["FIXED_PRICE"],
        "itemLocation": {"city": "London", "country": "GB"},
        "seller": {"username": "seller"},
        "image": {"imageUrl": "https://i.ebayimg.com/images/g/abc/s-l1600.jpg"},
        "itemCreationDate": "2026-09-01T10:00:00.000Z",
    }
    base.update(overrides)
    return base


def test_ebay_missing_credentials_are_reported_without_network(tmp_path, monkeypatch):
    # A developer running the suite may have real keys in .env. This test is
    # about the unconfigured path, so isolate it from local credentials.
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(
        "app.collectors.ebay.credential_store.env_file", tmp_path / "missing.env"
    )
    db = Database(":memory:")
    collector = EbayCollector(db)
    try:
        available, reason = collector.available
        assert available is False
        assert reason and "developer" in reason.lower()
        outcome = asyncio.run(run_queries(collector, ["rtx 4070"]))
        assert outcome.status == "not_configured"
        assert outcome.items == []
    finally:
        asyncio.run(collector.close())
        db.close()


def test_ebay_parser_rejects_wrong_currency_and_bad_urls_but_keeps_auctions():
    db = Database(":memory:")
    collector = EbayCollector(db)
    try:
        assert collector._parse_item(_ebay_item(price={"value": "450", "currency": "USD"}), "x") is None
        assert collector._parse_item(_ebay_item(price={"value": "450"}), "x") is None
        auction = collector._parse_item(
            _ebay_item(
                buyingOptions=["AUCTION"],
                currentBidPrice={"value": "120.00", "currency": "GBP"},
                bidCount=7,
                itemEndDate="2026-10-23T18:05:00.000Z",
            ),
            "x",
        )
        assert auction is not None
        assert auction.is_auction is True
        assert auction.price == 120.0
        assert auction.current_bid == 120.0
        assert auction.bid_count == 7
        assert auction.auction_end_at == "2026-10-23T18:05:00.000Z"
        assert collector._parse_item(
            _ebay_item(buyingOptions=["CLASSIFIED"]), "x"
        ) is None
        assert collector._parse_item(_ebay_item(itemWebUrl="https://evil.example/item/123"), "x") is None
        assert collector._parse_item(_ebay_item(itemWebUrl="javascript:alert(1)"), "x") is None
        assert collector._parse_item(
            _ebay_item(shippingOptions=[{"shippingCost": {"value": "5", "currency": "USD"}}]), "x"
        ) is None
        assert collector._parse_item(_ebay_item(title=""), "x") is None
        item = collector._parse_item(
            _ebay_item(image={"imageUrl": "javascript:bad"}, condition="Very Good"), "x"
        )
        assert item is not None
        assert item.image_url is None
        assert item.condition_raw == "Very Good"
        assert item.shipping == 5.0
        assert item.price == 450.0
        assert item.url == "https://www.ebay.co.uk/itm/123"
        assert item.is_auction is False
        assert item.raw["currency"] == "GBP"
        assert item.raw["marketplace"] == "EBAY_GB"
    finally:
        asyncio.run(collector.close())
        db.close()


def test_ebay_duplicate_ingestion_is_idempotent(tmp_path):
    db = Database(tmp_path / "ebay-dupe.db")
    collector = EbayCollector(db)
    try:
        item = collector._parse_item(_ebay_item(), "rtx 4070")
        assert item is not None
        scanner = Scanner(db, collectors={})
        first = scanner._ingest([item], "ebay")
        second = scanner._ingest([item], "ebay")
        assert first == (1, 1, 0)
        assert second == (1, 0, 0)
        row = db.query_one("SELECT marketplace, currency, price, shipping FROM listings")
        assert row == {"marketplace": "ebay", "currency": "GBP", "price": 450.0, "shipping": 5.0}
    finally:
        asyncio.run(collector.close())
        db.close()


class _StubCollector(BaseCollector):
    marketplace = "vinted"
    label = "Stub Vinted"
    min_interval = 0.0

    async def search(self, queries, max_pages=1):
        return await run_queries(self, queries, max_pages)

    async def search_query(self, query, max_pages=1):
        item = RawListing(
            marketplace="vinted",
            external_id="stub-1",
            url="https://www.vinted.co.uk/items/stub-1",
            title="NVIDIA RTX 4070 12GB graphics card",
            price=250.0,
            condition_raw="Good",
            search_query=query,
        )
        return [item], 1, 200


def test_scan_records_stage_and_query_performance_metrics(tmp_path):
    db = Database(tmp_path / "metrics.db")
    stub = _StubCollector(db)
    try:
        save_scan_config(
            db,
            categories=all_leaf_keys(),
            min_price=0,
            max_price=25000,
            marketplaces={"vinted": True, "gumtree": True, "ebay": True},
            active_preset="all-electronics",
        )
        scanner = Scanner(db, collectors={"vinted": stub})
        outcome = asyncio.run(scanner._scan(stub))
        assert outcome.status == "ok"
        row = db.query_one("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1")
        assert row["items_found"] == 1
        assert row["items_new"] == 1
        assert row["duration_ms"] is not None
        detail = __import__("json").loads(row["detail"])
        assert {"collect", "ingest", "valuation"} <= set(detail["stage_ms"])
        assert detail["collector_ms"] >= 0
        assert detail["total_ms"] >= detail["collector_ms"]
        assert detail["listings_processed"] == 1
        assert detail["query_timings"]
        assert detail["query_timings"][0]["query"]
        assert detail["query_timings"][0]["items"] >= 1
        assert "ms" in detail["query_timings"][0]
        assert db.scalar("SELECT COUNT(*) FROM listings") == 1
    finally:
        asyncio.run(stub.close())
        db.close()


def test_interleaved_queries_cover_more_than_core_categories():
    from app.config import DISCOVERY_QUERIES
    from app.scanner import interleaved_queries

    queries = interleaved_queries()
    categories = {category for category, _ in queries}
    words = " ".join(query for _, query in queries).lower()
    assert {"gpu", "cpu", "ram", "ssd", "console", "laptop", "handheld", "retro", "motherboard"} <= categories
    assert "external ssd" in words
    assert "steam deck" in words
    assert "game boy" in words
    assert len(DISCOVERY_QUERIES) >= 10
    assert len(queries) == len(set(query for _, query in queries))
