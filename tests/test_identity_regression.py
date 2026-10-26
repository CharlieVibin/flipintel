"""Permanent real-listing regressions for identity, comparables and auctions.

The dataset in ``tests/fixtures/identity_regression.json`` contains titles taken
from the collected database, including the three confirmed misclassifications
and other historic false positives. It is deliberately small and explicit: each
case states the identity class, whether a product key is allowed, the condition
and the confidence range.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.collectors.base import RawListing
from app.collectors.ebay import EbayCollector
from app.comparables import comparable_rejection, key_capacity, split_pool
from app.db import Database
from app.identification import identify_listing
from app.pricing import PricingDatabase
from app.utils import iso

FIXTURE = Path(__file__).parent / "fixtures" / "identity_regression.json"
DATASET = json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DATASET, ids=[case["title"][:40] for case in DATASET])
def test_real_listing_identity_regressions(case):
    result = identify_listing(case["title"])
    if case.get("no_key"):
        assert result.product_key is None, case["title"]
    if case.get("kind"):
        assert result.kind == case["kind"], case["title"]
    if case.get("key_prefix"):
        assert result.product_key is not None, case["title"]
        assert result.product_key.startswith(case["key_prefix"]), (
            case["title"], result.product_key
        )
    if case.get("condition"):
        assert result.condition == case["condition"], case["title"]
    if "is_faulty" in case:
        assert result.is_faulty is case["is_faulty"], case["title"]
    for flag in case.get("flags_include", []):
        assert flag in result.flags, (case["title"], flag, result.flags)
    if "max_confidence" in case:
        assert result.confidence <= case["max_confidence"], case["title"]
    if "min_confidence" in case:
        assert result.confidence >= case["min_confidence"], case["title"]


def test_confirmed_misclassifications_cannot_hide_behind_similarity():
    """A high title similarity must never override contradictory identity."""
    card = identify_listing("SanDisk microSD Express Card for Nintendo Switch 2 - 512GB")
    assert card.product_key is None
    assert card.kind != "console"
    assert card.confidence == 0.0
    assert card.identity_level == "insufficient"

    surface = identify_listing(
        'Microsoft Surface Pro 7+ 12.3" i5-1135G7 8GB RAM 256GB NVMe Grade D Cracked LCD'
    )
    assert surface.kind == "tablet"
    assert surface.product_key is not None
    assert not surface.product_key.startswith("cpu:")

    desktop = identify_listing(
        "Dell Pro Max Tower T2 FCT2250 Intel Ultra 5 235 32GB DDR5 RAM 512GB SSD"
    )
    assert desktop.kind == "pc"
    assert not desktop.product_key.startswith("cpu:")
    # Naming an exact model is not the same as proving every variant.
    assert desktop.identity_level in {"limited", "moderate"}


def test_compatibility_target_is_not_the_sold_item():
    for title in (
        "Case for Nintendo Switch 2",
        "Controller for PS5",
        "Keyboard case for iPad Air",
    ):
        result = identify_listing(title)
        assert result.product_key is None, title


def test_media_card_is_rejected_from_a_console_comparable_pool():
    comp = {
        "title": "SanDisk microSD Express Card for Nintendo Switch 2 - 512GB",
        "price": 24.99,
        "marketplace": "ebay",
    }
    reason = comparable_rejection(comp, kind="console", product_key="console:switch-2")
    assert reason and ("media" in reason or "accessory" in reason)


def test_complete_system_is_rejected_from_a_loose_cpu_pool():
    comp = {
        "title": "Dell Pro Max Tower T2 FCT2250 Intel Ultra 5 235 32GB DDR5 RAM 512GB SSD",
        "price": 1200,
        "marketplace": "ebay",
    }
    reason = comparable_rejection(comp, kind="cpu", product_key="cpu:intel:ultra-5-235")
    assert reason and "complete system" in reason


def test_mini_pc_is_not_priced_as_its_internal_ssd():
    result = identify_listing(
        "Stone NT310-H - intel Pentium 3550M 2.3GHZ- 8GB Ram - 128GB ssd"
    )
    assert result.kind == "pc"
    assert result.product_key and result.product_key.startswith("pc:")


def test_form_factor_words_do_not_make_a_component_a_complete_system():
    # These are the shapes that emptied the storage and CPU pools: "desktop"
    # and "SFF" describe the part, while only a system family or a complete
    # configuration identifies a whole machine.
    for title, kind, key in (
        ("Desktop HDD 2000GB", "ssd", "ssd:sata:2000"),
        ('Toshiba 2TB 3.5" Desktop Internal Hard Drive (HDD)', "ssd", "ssd:sata:2048"),
        ("AMD Ryzen 7 7700 AM5 8-Core Desktop Processor", "cpu", "cpu:amd:ryzen-7-7700"),
        ("Kingston 8GB DDR5 5600MHz Desktop RAM - KCP556US6-8", "ram", "ram:ddr5:8gb:5600:desktop"),
        ("Asus GeForce GT 730 2GB GDDR5 sff multi-monitor", "gpu", "gpu:gt-730:2gb"),
    ):
        assert comparable_rejection(
            {"title": title, "price": 50, "marketplace": "ebay"},
            kind=kind,
            product_key=key,
        ) is None, title
    assert "complete system" in (
        comparable_rejection(
            {
                "title": "HP Pro desk 400 G6 SFF i5-9500 / 8gb / Windows 11",
                "price": 250,
                "marketplace": "ebay",
            },
            kind="cpu",
            product_key="cpu:intel:core-i5-9500",
        )
        or ""
    )


def test_capacity_mismatch_is_rejected_but_unknown_capacity_is_allowed():
    mismatch = {
        "title": "Apple iPhone 15 Pro 256GB Unlocked",
        "price": 700,
        "marketplace": "gumtree",
    }
    assert "capacity" in (
        comparable_rejection(
            mismatch, kind="phone", product_key="phone:iphone-15-pro-128gb"
        )
        or ""
    )
    unknown = {
        "title": "Apple iPhone 15 Pro Unlocked",
        "price": 650,
        "marketplace": "gumtree",
    }
    assert comparable_rejection(
        unknown, kind="phone", product_key="phone:iphone-15-pro-128gb"
    ) is None
    matching = {
        "title": "Apple iPhone 15 Pro 128GB Unlocked",
        "price": 650,
        "marketplace": "gumtree",
    }
    assert comparable_rejection(
        matching, kind="phone", product_key="phone:iphone-15-pro-128gb"
    ) is None
    assert key_capacity("phone:iphone-15-pro-128gb") == 128


def test_console_bundle_is_comparable_but_a_controller_is_not():
    bundle = {
        "title": "Microsoft Xbox Series S 512GB White Console with Wired Controller",
        "price": 260,
        "marketplace": "ebay",
    }
    assert (
        comparable_rejection(
            bundle, kind="console", product_key="console:xbox-series-s"
        )
        is None
    )
    controller = {
        "title": "PS5 DualSense Controller White",
        "price": 45,
        "marketplace": "vinted",
    }
    assert "accessory" in (
        comparable_rejection(
            controller, kind="console", product_key="console:ps5"
        )
        or ""
    )


def test_split_pool_keeps_rejection_reasons():
    rows = [
        {"title": "RTX 4070 12GB graphics card", "price": 400, "marketplace": "ebay"},
        {"title": "Gaming PC RTX 4070 12GB 16GB RAM 1TB SSD", "price": 900, "marketplace": "ebay"},
        {"title": "RTX 4070 8GB graphics card", "price": 250, "marketplace": "vinted"},
    ]
    accepted, rejected = split_pool(rows, kind="gpu", product_key="gpu:rtx-4070-12gb")
    assert [row["price"] for row in accepted] == [400]
    assert {row["reason"].split(" ")[0] for row in rejected} == {"capacity", "complete"}


def test_changed_identity_invalidates_cached_valuation_without_deleting_history(tmp_path):
    db = Database(tmp_path / "snapshots.db")
    try:
        pricing = PricingDatabase(db)
        now = iso()
        db.execute(
            "INSERT INTO valuation_snapshots(product_key, kind, resale_good, confidence, "
            "evidence_level, refreshed_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("console:switch-2", "console", 338.27, 0.9, "limited", now),
        )
        assert pricing.latest("console:switch-2")["resale_good"] == 338.27
        assert pricing.invalidate(["console:switch-2"], "identity revised") == 1
        assert pricing.latest("console:switch-2") is None
        history = pricing.history("console:switch-2")
        assert len(history) == 1
        assert history[0]["invalidated_at"] is not None
        assert history[0]["invalid_reason"] == "identity revised"
        # Idempotent: a second pass does not rewrite the audit timestamp.
        assert pricing.invalidate(["console:switch-2"], "identity revised") == 0
    finally:
        db.close()


def test_observations_under_a_superseded_identity_stop_feeding_valuation(tmp_path):
    db = Database(tmp_path / "observations.db")
    try:
        listing_id = db.execute(
            "INSERT INTO listings(marketplace, external_id, url, title, price, "
            "listing_kind, product_key, first_seen, last_seen, status, created_at, updated_at) "
            "VALUES ('ebay', 'x1', 'https://www.ebay.co.uk/itm/x1', 'SanDisk microSD for Switch 2', "
            "24.99, 'other', NULL, ?, ?, 'active', ?, ?)",
            (iso(), iso(), iso(), iso()),
        ).lastrowid
        now = iso()
        day = datetime.now(timezone.utc).date().isoformat()
        for source_id, key, kind in (
            (f"listing:{listing_id}", "console:switch-2", "console"),
            ("listing:999999", "gpu:rtx-4070-12gb", "gpu"),
        ):
            db.execute(
                "INSERT INTO market_observations(product_key, kind, marketplace, source_id, "
                "title, price, observed_at, observed_day, created_at) "
                "VALUES (?, ?, 'ebay', ?, 'observed', 100, ?, ?, ?)",
                (key, kind, source_id, now, day, now),
            )
        pricing = PricingDatabase(db)
        assert pricing.invalidate_contaminated_observations() == 1
        rows = db.query(
            "SELECT product_key, invalidated_at FROM market_observations ORDER BY product_key"
        )
        by_key = {row["product_key"]: row["invalidated_at"] for row in rows}
        assert by_key["console:switch-2"] is not None
        # An observation whose listing still has that identity is untouched.
        assert by_key["gpu:rtx-4070-12gb"] is None
    finally:
        db.close()


class _StubEbay(EbayCollector):
    """Real category-auction method, deterministic Browse pages."""

    def __init__(self, pages):
        super().__init__(db=None)
        self.pages = pages
        self.calls: list[dict] = []
        self._token = "test"
        self._token_expires = 10**12

    @property
    def available(self):
        return True, None

    async def _access_token(self):
        return "test"

    async def _browse_request(self, params, query_label, **kwargs):
        self.calls.append(dict(params))
        index = int(params.get("offset", 0)) // int(params.get("limit", 200))
        return self.pages[index], len(self.pages[index]), 200


def _auction(index: int, minutes: int) -> RawListing:
    when = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return RawListing(
        marketplace="ebay",
        external_id=f"a{index}",
        url=f"https://www.ebay.co.uk/itm/a{index}",
        title=f"RTX 4070 12GB auction {index}",
        price=100.0,
        is_auction=True,
        auction_end_at=when.isoformat().replace("+00:00", "Z"),
        current_bid=100.0,
    )


@pytest.mark.asyncio
async def test_get_items_rechecks_at_most_twenty_known_auctions(tmp_path):
    db = Database(tmp_path / "recheck.db")

    class _Response:
        status_code = 200
        headers: dict[str, str] = {}

        def json(self):
            return {
                "itemId": "a1",
                "title": "RTX 4070 12GB",
                "itemWebUrl": "https://www.ebay.co.uk/itm/a1",
                "buyingOptions": ["AUCTION"],
                "currentBidPrice": {"value": "250.00", "currency": "GBP"},
                "bidCount": 7,
                "itemEndDate": "2026-10-23T20:00:00.000Z",
                "itemLocation": {"country": "GB"},
            }

    class _Client:
        def __init__(self):
            self.calls = []

        async def get(self, url, params=None, headers=None):
            self.calls.append((url, params))
            return _Response()

        async def aclose(self):
            return None

    collector = EbayCollector(db)
    fake = _Client()
    collector.client = fake
    collector._token = "test"
    collector._token_expires = 10**12
    collector._token_fingerprint = collector.credentials.fingerprint
    try:
        items, status = await collector.get_items([f"a{i}" for i in range(30)])
        assert status == 200
        assert len(fake.calls) == 20
        assert all("/buy/browse/v1/item/" in call[0] for call in fake.calls)
        assert items[0].external_id == "a1"
        assert items[0].current_bid == 250.0
        assert items[0].bid_count == 7
        assert items[0].is_auction is True
    finally:
        await collector.close()
        db.close()


def test_auction_filter_uses_the_live_purchase_budget():
    collector = EbayCollector(db=None)
    collector.scan_price_min = 10
    collector.scan_price_max = 100
    auction_filter = collector._auction_filter()
    assert "buyingOptions:{AUCTION}" in auction_filter
    assert "price:[10..100]" in auction_filter


@pytest.mark.asyncio
async def test_category_auction_scan_pages_only_inside_the_window():
    page_one = [_auction(i, 5) for i in range(200)]
    page_two = [_auction(200 + i, 25) for i in range(10)] + [
        _auction(300 + i, 90) for i in range(190)
    ]
    collector = _StubEbay([page_one, page_two])
    try:
        outcome = await collector.search_auction_categories(
            [("175673", "Computer components")],
            ending_within_minutes=30,
            max_requests=2,
        )
        assert outcome.status == "ok"
        assert outcome.detail["requests"] == 2
        assert outcome.detail["strategy"] == "category"
        assert len(outcome.items) == 210
        assert collector.calls[0]["category_ids"] == "175673"
        assert collector.calls[1]["offset"] == 200
        assert all(
            datetime.fromisoformat(item.auction_end_at.replace("Z", "+00:00"))
            <= datetime.now(timezone.utc) + timedelta(minutes=30)
            for item in outcome.items
        )
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_category_auction_scan_respects_the_request_budget():
    page = [_auction(i, 5) for i in range(200)]
    collector = _StubEbay([page, page, page])
    try:
        outcome = await collector.search_auction_categories(
            [("175673", "Components"), ("177", "Laptops")],
            ending_within_minutes=30,
            max_requests=1,
        )
        assert len(collector.calls) == 1
        assert outcome.detail["categories"] == ["Components"]
    finally:
        await collector.close()
