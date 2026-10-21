"""Focused regression tests for the parts most likely to create bad deals."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.collectors.ebay import EbayCollector
from app.db import Database
from app.identification import identify_listing
from app.main import create_app
from app.utils import iso
from app.valuation import ValuationConfig, ValuationEngine


def test_accessories_and_games_are_not_consoles():
    for title in (
        "Gulikit Steam Deck Oled joystick",
        "6in1 steam deck",
        "PS5 console faceplates",
        "Empty Nintendo switch box",
        "Lego world nintendo switch",
        "Brand new Nintendo switch turbo boost racing no offers",
    ):
        result = identify_listing(title)
        assert result.category != "console" or result.confidence == 0
        assert result.product_key is None


def test_complete_pc_is_not_a_loose_gpu():
    for title in ("AMD ryzen 7 rtx 3050", "I5 10th Gen RTX 3050 16gb RAM"):
        result = identify_listing(title)
        assert result.category == "pc"
        assert result.kind == "pc"
        assert result.product_key.startswith("pc:")


def test_console_titles_still_match():
    assert identify_listing("Nintendo Switch").category == "console"
    assert identify_listing("Xbox series x").category == "console"
    assert identify_listing("Sony PlayStation 5 Disc Console").category == "console"


def _listing(db: Database, *, external_id: str, title: str, price: float,
             key: str = "gpu:rtx-4070", kind: str = "gpu") -> int:
    now = iso()
    return db.execute(
        """
        INSERT INTO listings(
            marketplace, external_id, url, title, price, condition_norm,
            listing_kind, category, product_key, product_name,
            identification_confidence, first_seen, last_seen, status, created_at, updated_at
        ) VALUES ('vinted', ?, ?, ?, ?, 'good', ?, ?, ?, ?, 0.93, ?, ?, 'active', ?, ?)
        """,
        (
            external_id, f"https://www.vinted.co.uk/items/{external_id}", title, price,
            kind, kind, key, "NVIDIA GeForce RTX 4070", now, now, now, now,
        ),
    ).lastrowid


def test_implausibly_cheap_item_is_flagged_not_a_deal(tmp_path):
    db = Database(tmp_path / "valuation.db")
    try:
        for index in range(5):
            _listing(db, external_id=f"comp-{index}", title=f"RTX 4070 comp {index}", price=300)
        candidate_id = _listing(db, external_id="cheap", title="RTX 4070 bargain", price=60)
        candidate = db.query_one("SELECT * FROM listings WHERE id=?", (candidate_id,))
        assert candidate is not None
        candidate["flags_list"] = []
        result = ValuationEngine(db).evaluate(candidate, ValuationConfig.from_db(db))
        assert result["is_deal"] == 0
        assert any("manual review" in reason.lower() for reason in result["reasons"])
    finally:
        db.close()


def test_ebay_browse_item_parser():
    db = Database(":memory:")
    collector = EbayCollector(db)
    try:
        item = collector._parse_item(
            {
                "title": "NVIDIA RTX 4070 Founders Edition",
                "itemId": "v1|123|0",
                "itemWebUrl": "https://www.ebay.co.uk/itm/123?campid=1",
                "price": {"value": "450.00", "currency": "GBP"},
                "shippingOptions": [{"shippingCost": {"value": "5.00"}}],
                "condition": "Used",
                "buyingOptions": ["FIXED_PRICE"],
                "itemLocation": {"city": "London", "country": "GB"},
                "seller": {"username": "seller"},
            },
            "rtx 4070",
        )
        assert item is not None
        assert item.price == 450.0
        assert item.shipping == 5.0
        assert item.url == "https://www.ebay.co.uk/itm/123"
        assert item.marketplace == "ebay"
    finally:
        asyncio.run(collector.close())
        db.close()


def test_api_health_and_empty_feed(tmp_path):
    db = Database(tmp_path / "api.db")
    app = create_app(db=db, autostart=False)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        body = health.json()
        assert body["status"] == "ok"
        assert {source["marketplace"] for source in body["scanner"]["sources"]} == {
            "vinted", "gumtree", "ebay", "facebook"
        }
        feed = client.get("/api/deals")
        assert feed.status_code == 200
        assert feed.json()["deals"] == []
