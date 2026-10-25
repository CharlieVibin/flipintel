"""Review-queue eligibility regressions.

These cover the real dashboard failures that survived Phase 6: a cooler sold
"no CPU" priced as the processor, a 1TB-128TB variation listing priced as a
1TB drive, the same seller's relists each becoming a review card, and weak or
unprofitable uncertainty being turned into manual work.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.db import Database
from app.dedup import mark_duplicates
from app.eligibility import EXCLUDED, NEEDS_VERIFICATION, assess
from app.identification import identify_listing
from app.main import create_app
from app.utils import iso
from app.valuation import ValuationEngine


def _listing(db: Database, *, external_id: str, title: str, price: float,
             seller: str = "", key: str | None = None, kind: str = "cpu",
             flags: list[str] | None = None, identity_level: str = "moderate",
             confidence: float = 0.7) -> int:
    now = iso()
    return int(db.execute(
        """
        INSERT INTO listings(
            marketplace, external_id, url, title, price, condition_norm,
            listing_kind, category, product_key, product_name, seller,
            identification_confidence, identity_level, flags, attrs,
            first_seen, last_seen, status, created_at, updated_at
        ) VALUES ('ebay', ?, ?, ?, ?, 'good', ?, ?, ?, 'Test product', ?, ?, ?, ?, '{}',
                  ?, ?, 'active', ?, ?)
        """,
        (
            external_id, f"https://www.ebay.co.uk/itm/{external_id}", title, price,
            kind, kind, key, seller, confidence, identity_level,
            json.dumps(flags or []), now, now, now, now,
        ),
    ).lastrowid)


def _row(**overrides) -> dict:
    base = {
        "marketplace": "ebay", "sale_role": "component", "product_key": "cpu:amd:ryzen-7-3700x",
        "flags_list": [], "is_faulty": 0, "is_wanted": 0, "is_bundle": 0,
        "duplicate_of": None,
    }
    base.update(overrides)
    return base


def test_cooler_without_cpu_is_not_the_processor():
    cooler = identify_listing("AMD Ryzen 7 3700X COOLER/FAN ONLY no CPU")
    assert cooler.product_key is None
    assert cooler.sale_role == "parts"
    assert "parts_only" in cooler.flags

    for title in (
        "AMD Ryzen 7 3700X fan only",
        "Wraith Prism cooler only AM4",
        "AM4 mounting bracket only no cpu",
    ):
        result = identify_listing(title)
        assert result.product_key is None, title
        assert result.sale_role in {"parts", "accessory"}, title

    # The processor is included here, so it really is the CPU.
    included = identify_listing("AMD Ryzen 7 3700X with Wraith Prism cooler")
    assert included.product_key == "cpu:amd:ryzen-7-3700x"
    assert included.sale_role == "component"

    # A motherboard that needs a CPU is still a motherboard, not a CPU.
    board = identify_listing("Gigabyte Z790 AORUS Master X motherboard no CPU")
    assert board.product_key and board.product_key.startswith("mobo:")
    assert board.sale_role == "component"


def test_named_games_are_not_consoles():
    for title in (
        "Donkey Kong Bananza Switch 2 - Perfect Condition!",
        "W2K20 Xbox one X",
        "Planet Coaster Console Edition PS5 - Brand New & Sealed Loose Disc",
        "Mario Kart 8 Deluxe Nintendo Switch",
    ):
        result = identify_listing(title)
        assert result.product_key is None, title
        assert result.sale_role == "game", title
    for title in ("Nintendo Switch 2 Console", "PS5 Console Disc Edition 825GB"):
        assert identify_listing(title).sale_role == "device", title


def test_storage_media_and_drive_trays_are_not_the_device():
    card = identify_listing(
        "32MB PSP Sony Memory Card Stick Duo For Sony PSP 1003 2004 3000 Console UK"
    )
    assert card.product_key is None
    assert card.sale_role == "media"

    tray = identify_listing("DELL Equallogic 4TB HDD Tray")
    assert tray.product_key is None
    assert tray.sale_role == "accessory"

    # The console itself is still identified.
    assert identify_listing("Sony PSP 3000 Console").product_key == "console:psp"


def test_capacity_range_storage_has_no_exact_identity():
    for title in (
        "1TB-128TB Portable External SSD USB 3.1 Type-C Hard Drive PC Mac",
        "1TB-4TB Portable External SSD USB 3.0 Type-C Fast Storage Plug Play",
    ):
        result = identify_listing(title)
        assert result.product_key is None, title
        assert "capacity_range" in result.flags
        assert "suspicious_capacity" in result.flags
        role, reason = assess(
            _row(product_key=None, sale_role="component",
                 flags_list=result.flags),
            is_deal=False, needs_review=True, expected_resale=39.66,
            purchase_total=16.99, net_profit=17.0,
        )
        assert role == EXCLUDED
        assert reason


def test_unbranded_portable_storage_is_flagged_but_branded_storage_is_kept():
    generic = identify_listing("1TB Portable External SSD USB 3.1")
    assert "unbranded_storage" in generic.flags

    branded = identify_listing("Samsung T7 1TB Portable External SSD USB 3.2")
    assert branded.product_key == "ssd:usb:external:1tb:samsung"
    assert "unbranded_storage" not in branded.flags
    role, _ = assess(
        _row(product_key=branded.product_key, sale_role="component"),
        is_deal=False, needs_review=True, expected_resale=110,
        purchase_total=55, net_profit=35, min_profit=20, min_roi=0.12,
        identity_level="moderate", confidence=0.7,
    )
    assert role == NEEDS_VERIFICATION


def test_review_requires_meaningful_margin_and_resolvable_identity():
    thin, reason = assess(
        _row(),
        is_deal=False, needs_review=True, expected_resale=60,
        purchase_total=52, net_profit=3, min_profit=20, min_roi=0.12,
        identity_level="moderate", confidence=0.7,
    )
    assert thin == EXCLUDED and "justifies manual review" in (reason or "")

    weak, reason = assess(
        _row(), is_deal=False, needs_review=True, expected_resale=200,
        purchase_total=80, net_profit=80, min_profit=20, min_roi=0.12,
        identity_level="moderate", confidence=0.2,
    )
    assert weak == EXCLUDED and "too weak" in (reason or "")

    family, _ = assess(
        _row(), is_deal=False, needs_review=True, expected_resale=120,
        purchase_total=90, net_profit=26, min_profit=20, min_roi=0.12,
        identity_level="limited", confidence=0.55,
    )
    assert family == EXCLUDED

    genuine, _ = assess(
        _row(product_key="gpu:rtx-4070-ti", flags_list=["variant_unclear"]),
        is_deal=False, needs_review=True, expected_resale=380,
        purchase_total=210, net_profit=110, min_profit=20, min_roi=0.12,
        identity_level="moderate", confidence=0.68,
    )
    assert genuine == NEEDS_VERIFICATION


def test_duplicate_flags_are_excluded_by_role_and_identity():
    role, reason = assess(
        _row(duplicate_of=12), is_deal=False, needs_review=True,
        expected_resale=200, purchase_total=50, net_profit=90,
    )
    assert role == EXCLUDED and "Duplicate" in (reason or "")

    role, reason = assess(
        _row(sale_role="parts"), is_deal=True, needs_review=False,
        expected_resale=200, purchase_total=50, net_profit=90,
    )
    assert role == EXCLUDED and reason


def test_same_seller_relist_is_linked_but_other_sellers_are_not(tmp_path):
    db = Database(tmp_path / "dedup.db")
    try:
        first = _listing(db, external_id="a1", seller="beyondbordertrade",
                         title="1TB External SSD Portable", price=16.99)
        second = _listing(db, external_id="a2", seller="beyondbordertrade",
                          title="1TB External SSD Portable", price=16.99)
        other = _listing(db, external_id="a3", seller="someone-else",
                         title="1TB External SSD Portable", price=16.99)
        same_model = _listing(db, external_id="a4", seller="third-seller",
                              title="Samsung T7 1TB Portable SSD", price=70,
                              key="ssd:usb:external:1tb:samsung", kind="ssd")
        other_model = _listing(db, external_id="a5", seller="fourth-seller",
                               title="Samsung T7 2TB Portable SSD", price=120,
                               key="ssd:usb:external:2tb:samsung", kind="ssd")
        marked = mark_duplicates(db)
        assert marked >= 1
        linked = db.query_one("SELECT duplicate_of FROM listings WHERE id=?", (second,))
        assert linked["duplicate_of"] == first
        assert db.query_one("SELECT duplicate_of FROM listings WHERE id=?", (other,))["duplicate_of"] is None
        assert db.query_one("SELECT duplicate_of FROM listings WHERE id=?", (same_model,))["duplicate_of"] is None
        assert db.query_one("SELECT duplicate_of FROM listings WHERE id=?", (other_model,))["duplicate_of"] is None
        # Rows are linked, never merged or removed.
        assert db.scalar("SELECT COUNT(*) FROM listings") == 5
        assert mark_duplicates(db) == 0
    finally:
        db.close()


def test_review_api_only_returns_the_authoritative_state(tmp_path):
    db = Database(tmp_path / "review-api.db")
    try:
        visible = _listing(db, external_id="rv1", title="Genuine RTX 4070 Ti",
                           price=300, key="gpu:rtx-4070-ti", kind="gpu")
        stale = _listing(db, external_id="rv2", title="Ryzen 7 3700X cooler only",
                         price=21, key="cpu:amd:ryzen-7-3700x")
        legacy = _listing(db, external_id="rv3", title="Old row", price=50)
        for listing_id, eligibility in (
            (visible, "needs_verification"), (stale, "excluded"), (legacy, None)
        ):
            db.execute(
                "INSERT INTO deals(listing_id, needs_review, eligibility, computed_at) "
                "VALUES (?, 1, ?, ?)",
                (listing_id, eligibility, iso()),
            )
        app = create_app(db=db, autostart=False)
        with TestClient(app) as client:
            body = client.get("/api/deals?review=1").json()
            assert [row["id"] for row in body["deals"]] == [visible]
            assert body["total"] == 1
            stats = client.get("/api/overview").json()
            assert stats["review_count"] == 1
    finally:
        db.close()


def test_valuation_clears_review_when_the_item_becomes_ineligible(tmp_path):
    db = Database(tmp_path / "valuation-review.db")
    try:
        listing_id = _listing(
            db, external_id="cool1",
            title="AMD Ryzen 7 3700X COOLER/FAN ONLY no CPU", price=21.49,
        )
        # Simulate the existing contaminated stored state.
        db.execute(
            "UPDATE listings SET product_key='cpu:amd:ryzen-7-3700x', "
            "flags='[\"parts_only\",\"accessory_or_box\"]', sale_role='parts' "
            "WHERE id=?",
            (listing_id,),
        )
        db.execute(
            "INSERT INTO deals(listing_id, product_key, kind, needs_review, "
            "eligibility, computed_at) VALUES (?, 'cpu:amd:ryzen-7-3700x', 'cpu', 1, "
            "'needs_verification', ?)",
            (listing_id, iso()),
        )
        ValuationEngine(db).refresh()
        row = db.query_one(
            "SELECT eligibility, needs_review, is_deal FROM deals WHERE listing_id=?",
            (listing_id,),
        )
        assert row["eligibility"] == "excluded"
        assert row["needs_review"] == 0
        assert row["is_deal"] == 0
    finally:
        db.close()
