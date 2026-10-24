"""CeX identity matching, permanent price memory, resale strategies and flips."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from app.db import Database
from app.flips import FlipBook, resale_factors
from app.notifications import Notifier
from app.pricing import PricingDatabase
from app.pricing_cex import CexPricing, CexRow, match_score, select_quote
from app.utils import iso, parse_iso, utcnow
from app.valuation import ValuationConfig, ValuationEngine, seller_weight


def _row(name: str, sell: float, box: str, grade: str = "") -> CexRow:
    return CexRow(
        name=name, grade=grade, sell_gbp=sell, cash_gbp=round(sell * 0.65, 2),
        voucher_gbp=round(sell * 0.75, 2), cash_pct=65, voucher_pct=75,
        box_id=box, product_id=box[:-1] if grade else box, category="Consoles",
        super_category="Gaming", stores=(), online_qty=1, previous_gbp=sell,
        price_changed="", discontinued=False, origin="UK",
    )


# --- CeX identity ---------------------------------------------------------

def test_cex_model_letters_are_not_dropped():
    # Dropping one-character tokens made Xbox Series X and Series S identical.
    assert match_score("Xbox Series X", "Xbox Series S Console, 1TB, Black, Boxed") == 0.0
    assert match_score("Xbox Series X", "Xbox Series X Console, 1TB, Black, Boxed") == 1.0
    assert match_score("RTX 4060", "Gigabyte RTX 4060 Ti 8GB") == 0.0
    assert match_score("RTX 4060 Ti", "Gigabyte RTX 4060 8GB") == 0.0


def test_cex_chooses_plain_product_over_special_edition_and_packaging():
    rows = [
        _row("Xbox Series X Console, 1TB, Black, Unboxed", 550, "SXSX1TBB002"),
        _row("Xbox Series X Console, 1TB, Black, Boxed", 570, "SXSX1TBB001"),
        _row("Xbox Series X Console, 1TB, Halo Infinite Black, Boxed", 700, "SXSX1TBHB001"),
        _row("Xbox Series X All-Digital Console, 1TB, Robot White, Boxed", 550, "SXSXAD1TBRW001"),
        _row("Xbox Series S Console, 512GB, White, Boxed", 330, "SXSS512GBW001"),
        _row("Value Xbox Series X Vertical Stand", 4, "SXSVAL09"),
    ]
    selection = select_quote(rows, "Xbox Series X", "good", "console")
    assert selection.status == "ok"
    assert selection.sell == 570.0
    assert "Halo" in (selection.note or "")
    assert "Vertical" not in selection.name


def test_cex_rejects_an_ambiguous_cohort_instead_of_blending():
    rows = [
        _row("Switch Console, 64GB OLED + White Joy-Con, Boxed", 190, "A1"),
        _row("Switch Console, 64GB OLED + Neon Joy-Con, Boxed", 120, "B1"),
        _row("Switch Console, 64GB OLED + Red Joy-Con, Boxed", 240, "C1"),
    ]
    selection = select_quote(rows, "Nintendo Switch OLED", "good", "console")
    assert selection.status == "ambiguous"
    assert selection.ambiguous is True
    assert selection.sell is None


def test_cex_negative_results_are_cached_and_failures_retry():
    db = Database(":memory:")
    try:
        cex = CexPricing(db)
        now = iso()
        db.execute(
            "INSERT INTO external_quotes(product_key, source, title, status, fetched_at, checked_at) "
            "VALUES ('monitor:24in', 'cex', NULL, 'no_match', ?, ?)",
            (now, now),
        )
        assert cex.needs_refresh("monitor:24in") is False
        db.execute(
            "INSERT INTO external_quotes(product_key, source, title, status, fetched_at, checked_at) "
            "VALUES ('gpu:missing', 'cex', NULL, 'failed', ?, ?)",
            (now, now),
        )
        assert cex.needs_refresh("gpu:missing") is True
        db.execute(
            "UPDATE external_quotes SET checked_at=? WHERE product_key='monitor:24in'",
            (iso(utcnow().replace(year=utcnow().year - 1)),),
        )
        assert cex.needs_refresh("monitor:24in") is True
    finally:
        db.close()


def test_auction_search_keeps_only_items_inside_the_window(monkeypatch):
    from app.collectors.base import RawListing
    from app.collectors.ebay import EbayCollector

    db = Database(":memory:")
    collector = EbayCollector(db)
    monkeypatch.setattr(type(collector), "available", property(lambda self: (True, None)))

    def make(item_id: str, minutes: int) -> RawListing:
        return RawListing(
            marketplace="ebay",
            external_id=item_id,
            url=f"https://www.ebay.co.uk/itm/{item_id}",
            title="Xbox Series X console",
            price=150.0,
            is_auction=True,
            auction_end_at=iso(utcnow() + timedelta(minutes=minutes)),
            current_bid=150.0,
            bid_count=3,
        )

    async def fake_browse(params, label):
        assert params["sort"] == "endingSoonest"
        assert "AUCTION" in params["filter"]
        return [make("soon", 12), make("later", 90)], 2, 200

    collector._browse_request = fake_browse  # type: ignore[assignment]
    try:
        outcome = asyncio.run(
            collector.search_auctions(["xbox series x console"], ending_within_minutes=30)
        )
        assert outcome.status == "ok"
        assert [item.external_id for item in outcome.items] == ["soon"]
        assert outcome.detail["kind"] == "ending_soon"
    finally:
        asyncio.run(collector.close())
        db.close()


class _BlockedClient:
    async def post(self, *args, **kwargs):
        request = httpx.Request("POST", "https://search.webuy.io")
        return httpx.Response(403, request=request, json={})

    async def aclose(self):
        return None


def test_cex_403_is_reported_and_never_invented_as_a_price():
    db = Database(":memory:")
    cex = CexPricing(db)
    cex.client = _BlockedClient()
    try:
        result = asyncio.run(cex.refresh_quote("console:ps5", "PlayStation 5", "good", {}))
        assert result["status"] == "blocked"
        assert "sell" not in result
        assert cex.health()["status"] == "blocked"
        quote = db.query_one("SELECT status, sell_price FROM external_quotes")
        assert quote == {"status": "blocked", "sell_price": None}
        assert db.scalar("SELECT COUNT(*) FROM cex_price_history") == 0
    finally:
        asyncio.run(cex.close())
        db.close()


# --- price memory ---------------------------------------------------------

def _listing(db: Database, i: int, price: float, key: str = "console:xbox-series-x") -> None:
    now = iso()
    db.execute(
        """
        INSERT INTO listings(marketplace, external_id, title, url, price, condition_norm,
            product_key, product_name, listing_kind, identification_confidence, seller,
            raw_json, first_seen, last_seen, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'good', ?, 'Xbox Series X', 'console', 0.9, ?, '{}',
                ?, ?, 'active', ?, ?)
        """,
        ("vinted", f"x{i}", f"Xbox Series X {i}", f"http://x/{i}", price, key,
         f"seller{i}", now, now, now, now),
    )


def test_price_memory_reuses_for_seven_days_then_goes_stale():
    db = Database(":memory:")
    try:
        pricing = PricingDatabase(db)
        pricing.record("console:xbox-series-x", "console", _Estimate(), {"vinted": 5})
        assert pricing.is_stale(pricing.latest("console:xbox-series-x")) is False
        assert len(pricing.history("console:xbox-series-x")) == 1
        # Age the snapshot past the reuse window without deleting it.
        old = iso(utcnow().replace(year=utcnow().year - 1))
        db.execute("UPDATE valuation_snapshots SET refreshed_at=?", (old,))
        assert pricing.is_stale(pricing.latest("console:xbox-series-x")) is True
        _listing(db, 1, 300)
        stale = pricing.stale_products(limit=5, priority=["console:xbox-series-x"])
        assert [row["product_key"] for row in stale] == ["console:xbox-series-x"]
    finally:
        db.close()


def test_price_memory_keeps_history_and_suppresses_duplicate_claims():
    db = Database(":memory:")
    try:
        pricing = PricingDatabase(db)
        assert pricing.record("gpu:rtx-4060-8gb", "gpu", _Estimate(300), {"vinted": 4}) is True
        assert pricing.record("gpu:rtx-4060-8gb", "gpu", _Estimate(300), {"vinted": 4}) is False
        assert pricing.record("gpu:rtx-4060-8gb", "gpu", _Estimate(280), {"vinted": 6}) is True
        history = pricing.history("gpu:rtx-4060-8gb")
        assert [row["resale_good"] for row in history] == [280, 300]
        assert pricing.claim("gpu:rtx-4060-8gb") is True
        assert pricing.claim("gpu:rtx-4060-8gb") is False
        pricing.release("gpu:rtx-4060-8gb", "ok")
        assert pricing.claim("gpu:rtx-4060-8gb") is True
    finally:
        db.close()


class _Estimate:
    def __init__(self, resale: float = 300) -> None:
        self.resale_good = resale
        self.low = resale * 0.9
        self.high = resale * 1.1
        self.confidence = 0.5
        self.evidence_level = "limited"
        self.sample_size = 5
        self.sold_count = 0
        self.method = "live_asking_comps"
        self.dispersion = 0.1
        self.strategies = {
            "quick": {"expected": resale * 0.95, "ask": resale * 0.95},
            "balanced": {"expected": resale, "ask": resale * 1.03},
            "max": {"expected": resale * 1.05, "ask": resale * 1.12},
        }
        self.sources = {"vinted": 5}


# --- evidence weighting and strategies ------------------------------------

def test_established_seller_weight_and_cex_conversion():
    assert seller_weight(json.dumps({"seller_feedback": "99.4%", "seller_feedback_score": 900})) == 2.0
    assert seller_weight(json.dumps({"seller_feedback": "97.0%", "seller_feedback_score": 20})) == 1.0
    assert seller_weight(json.dumps({"seller_feedback": "92.0%", "seller_feedback_score": 900})) == 0.5
    assert seller_weight(None) == 1.0

    db = Database(":memory:")
    try:
        for i, price in enumerate([280, 300, 320, 340, 360], 1):
            _listing(db, i, price)
        now = iso()
        db.execute(
            """
            INSERT INTO external_quotes(product_key, source, title, sell_price,
                cash_price, voucher_price, grade, sample_count, status, fetched_at, checked_at)
            VALUES ('console:xbox-series-x', 'cex', 'Xbox Series X Boxed', 600, 380, 430,
                    '', 3, 'ok', ?, ?)
            """,
            (now, now),
        )
        engine = ValuationEngine(db)
        config = ValuationConfig.from_db(db)
        result = engine.evaluate(
            db.query_one("SELECT * FROM listings WHERE id=1") | {"flags_list": []}, config
        )
        notes = " ".join(result["breakdown"]["valuation_notes"])
        assert "observed asking market" in notes
        assert result["breakdown"]["evidence"]["sources"].get("cex") == 1
        strategies = result["breakdown"]["resale"]["strategies"]
        assert strategies["quick"]["expected"] <= strategies["balanced"]["expected"]
        assert strategies["max"]["expected"] >= strategies["balanced"]["expected"]
        assert result["breakdown"]["resale"]["initial_ask"] >= strategies["balanced"]["expected"]
        assert result["evidence_level"] in {"limited", "moderate"}
    finally:
        db.close()


def test_asking_only_confidence_varies_with_evidence_instead_of_a_fixed_value():
    db = Database(":memory:")
    try:
        for i, price in enumerate([290, 300, 310, 320, 330], 1):
            _listing(db, i, price)
        config = ValuationConfig.from_db(db)
        engine = ValuationEngine(db)
        small = engine.evaluate(
            db.query_one("SELECT * FROM listings WHERE id=1") | {"flags_list": []}, config
        )
        for i, price in enumerate([290, 300, 310, 320, 330, 340, 350, 360, 370, 380], 11):
            _listing(db, i, price)
        engine._comp_cache = None
        large = engine.evaluate(
            db.query_one("SELECT * FROM listings WHERE id=1") | {"flags_list": []}, config
        )
        assert small["confidence"] != 0.52
        assert large["confidence"] > small["confidence"]
        assert large["evidence_level"] != "insufficient"
    finally:
        db.close()


def test_auction_maximum_bid_still_clears_profit_and_roi():
    db = Database(":memory:")
    try:
        for i, price in enumerate([280, 300, 320, 340, 360], 1):
            _listing(db, i, price)
        now = iso()
        db.execute(
            """
            INSERT INTO listings(marketplace, external_id, title, url, price, shipping,
                condition_norm, product_key, product_name, listing_kind,
                identification_confidence, is_auction, current_bid, bid_count,
                auction_end_at, first_seen, last_seen, status, created_at, updated_at)
            VALUES ('ebay','auc1','Xbox Series X auction','http://e/1', 120, 5,
                    'good','console:xbox-series-x','Xbox Series X','console',0.9,1,120,4,
                    ?,?,?,'active',?,?)
            """,
            (iso(utcnow()), now, now, now, now),
        )
        engine = ValuationEngine(db)
        engine.refresh()
        deal = db.query_one("SELECT * FROM deals WHERE listing_id = 6")
        assert deal["max_bid"] and deal["max_bid"] > 0
        # At the ceiling the deal should not exceed the profit requirement.
        ceiling = deal["max_bid"]
        resale = deal["expected_resale"]
        proceeds = resale - (resale * 0.1322 + 0.30) - 4.50 - 1.00
        assert round(proceeds - (ceiling + 5), 2) >= -0.01
        assert deal["resale_strategy"] == "balanced"
        assert deal["estimated_days"]
        assert deal["valuation_refreshed_at"]
    finally:
        db.close()


# --- personal flips -------------------------------------------------------

def test_flip_preserves_prediction_and_learns_only_after_three_outcomes():
    db = Database(":memory:")
    try:
        _listing(db, 1, 200)
        engine = ValuationEngine(db)
        engine.refresh()
        book = FlipBook(db)
        flip = book.start_from_listing(
            1, purchase_price=200, purchase_shipping=5, preparation_cost=3
        )
        assert flip["predicted_resale"] == db.query_one(
            "SELECT expected_resale FROM deals WHERE listing_id=1"
        )["expected_resale"]
        assert flip["predicted_profit"] is not None
        flip = book.update(
            flip["id"],
            {"status": "sold", "final_price": 280, "fees": 36, "postage": 5},
        )
        assert flip["net_profit"] == round(280 - 36 - 5 - 208, 2)
        assert flip["prediction_error"] is not None
        # One outcome must not change anything.
        assert resale_factors(db) == {}
        for index in range(2, 7):
            _listing(db, index, 200 + index)
            other = book.start_manual(
                {
                    "title": f"Xbox Series X {index}",
                    "kind": "console",
                    "purchase_price": 200,
                    "predicted_resale": 300,
                    "predicted_profit": 50,
                }
            )
            book.update(
                other["id"],
                {"status": "sold", "final_price": 100, "fees": 0, "postage": 0},
            )
        factors = resale_factors(db)
        assert "console" in factors
        assert 0.90 <= factors["console"] <= 1.08
        summary = book.summary()
        assert summary["counts"]["sold"] == 6
        assert summary["prediction_mae"] is not None
        assert summary["realised_profit"] < 0
        assert summary["learned_factors"]["console"] == factors["console"]
    finally:
        db.close()


def test_auction_alerts_wait_for_the_close_margin():
    """A good auction two dozen minutes out is silent; near the close it alerts."""
    db = Database(":memory:")
    try:
        for i in range(4):
            _listing(db, i, 250)
        now = iso()
        auction_id = db.execute(
            """
            INSERT INTO listings(marketplace, external_id, title, url, price, condition_norm,
                product_key, product_name, listing_kind, identification_confidence, is_auction,
                current_bid, bid_count, auction_end_at, first_seen, last_seen, status,
                created_at, updated_at)
            VALUES ('ebay','a9','Xbox Series X auction','http://e/a9', 120, 'good',
                'console:xbox-series-x','Xbox Series X','console',0.9,1,120,4,?,?,?,'active',?,?)
            """,
            (iso(utcnow() + timedelta(minutes=25)), now, now, now, now),
        ).lastrowid
        ValuationEngine(db).refresh()
        db.execute(
            "UPDATE deals SET is_deal=1, needs_review=0, max_bid=400, "
            "evidence_level='moderate' WHERE listing_id=?",
            (auction_id,),
        )
        db.set_settings(
            {
                "notifications_enabled": "true",
                "discord_webhook_url": "https://discord.com/api/webhooks/1/abc",
                "auction_window_minutes": "30",
                "auction_alert_window_minutes": "15",
            }
        )
        notifier = Notifier(db)
        sent: list[str] = []

        async def fake_send(webhook, *, content, embed, listing_id=None, channel="discord", record_notification=True):
            sent.append(content)
            return True

        notifier._send = fake_send  # type: ignore[assignment]
        try:
            assert asyncio.run(notifier.notify_auctions(limit=4)) == 0
            db.execute(
                "UPDATE listings SET auction_end_at=? WHERE id=?",
                (iso(utcnow() + timedelta(minutes=12)), auction_id),
            )
            assert asyncio.run(notifier.notify_auctions(limit=4)) == 1
            assert "ending soon" in sent[0].lower()
        finally:
            asyncio.run(notifier.close())
    finally:
        db.close()


def test_flip_price_changes_and_unsold_items_are_tracked():
    db = Database(":memory:")
    try:
        book = FlipBook(db)
        flip = book.start_manual(
            {"title": "RTX 3080", "kind": "gpu", "purchase_price": 250,
             "predicted_resale": 340, "predicted_profit": 60}
        )
        book.change_price(flip["id"], 360)
        book.change_price(flip["id"], 330)
        flip = book.update(flip["id"], {"status": "unsold", "feedback": -1,
                                        "feedback_note": "No buyers at 330"})
        assert [change["price"] for change in flip["price_changes"]] == [360, 330]
        assert flip["status"] == "unsold"
        assert flip["feedback"] == -1
        assert "price_changes" in book.list("unsold")[0]
    finally:
        db.close()


# --- notifications --------------------------------------------------------

def test_auction_alerts_deduplicate_and_respect_cooldown():
    db = Database(":memory:")
    try:
        _listing(db, 1, 250)
        now = iso()
        db.execute(
            """
            INSERT INTO listings(marketplace, external_id, title, url, price, condition_norm,
                product_key, product_name, listing_kind, identification_confidence, is_auction,
                current_bid, bid_count, auction_end_at, first_seen, last_seen, status,
                created_at, updated_at)
            VALUES ('ebay','a1','Xbox Series X auction','http://e/a1', 120, 'good',
                'console:xbox-series-x','Xbox Series X','console',0.9,1,120,4,?,?,?,'active',?,?)
            """,
            (iso(utcnow() + timedelta(minutes=25)), now, now, now, now),
        )
        engine = ValuationEngine(db)
        engine.refresh()
        db.execute(
            "UPDATE deals SET is_deal=1, needs_review=0, max_bid=400, "
            "evidence_level='moderate' "
            "WHERE listing_id=(SELECT id FROM listings WHERE external_id='a1')"
        )
        db.set_settings(
            {
                "notifications_enabled": "true",
                "discord_webhook_url": "https://discord.com/api/webhooks/1/abc",
                "notification_cooldown_minutes": "180",
                # This test is about deduplication, so keep the close margin
                # wide enough for its 25-minute fixture.
                "auction_alert_window_minutes": "30",
            }
        )
        notifier = Notifier(db)
        sent: list[str] = []

        async def fake_send(webhook, *, content, embed, listing_id=None, channel="discord", record_notification=True):
            sent.append(content)
            return True

        notifier._send = fake_send  # type: ignore[assignment]
        try:
            assert asyncio.run(notifier.notify_auctions(limit=4, allowed_ids=set())) == 0
            assert asyncio.run(notifier.notify_auctions(limit=4)) == 1
            assert asyncio.run(notifier.notify_auctions(limit=4)) == 0
            assert len(sent) == 1
            assert "ending soon" in sent[0].lower()
            # Poor evidence or a non-promoted row must never trigger an alert,
            # even when its stored ceiling happens to sit above the bid.
            db.execute("UPDATE deals SET evidence_level='insufficient'")
            assert asyncio.run(notifier.notify_auctions(limit=4)) == 0
            db.execute("UPDATE deals SET evidence_level='moderate', is_deal=0")
            assert asyncio.run(notifier.notify_auctions(limit=4)) == 0
            db.execute("UPDATE deals SET is_deal=1")
            # A material bid increase is allowed through despite the cooldown.
            db.execute("UPDATE listings SET current_bid=160, price=160 WHERE external_id='a1'")
            assert asyncio.run(notifier.notify_auctions(limit=4)) == 1
            assert len(sent) == 2
        finally:
            asyncio.run(notifier.close())
    finally:
        db.close()
