"""Regressions for eBay discovery coverage and the three confirmed bugs."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from fastapi.testclient import TestClient

from app.collectors.ebay import EbayCollector
from app.comparables import comparable_rejection
from app.db import Database
from app.identification import identify_listing
from app.main import create_app
from app.scan_config import load_scan_config, save_scan_config
from app.scanner import Scanner
from app.utils import iso, utcnow
from app.variants import compare_variants, variant_from_title


def _raw(price: str = "49.99", bid: str = "12.00") -> dict:
    return {
        "itemId": "v1|123456789|0",
        "title": "NVIDIA GeForce RTX 4070 12GB Graphics Card",
        "itemWebUrl": "https://www.ebay.co.uk/itm/123456789",
        "buyingOptions": ["AUCTION", "FIXED_PRICE"],
        "price": {"value": price, "currency": "GBP"},
        "currentBidPrice": {"value": bid, "currency": "GBP"},
        "condition": "Used",
        "itemLocation": {"country": "GB"},
        "shippingOptions": [
            {"shippingCost": {"value": "4.50", "currency": "GBP"}}
        ],
        "seller": {"username": "seller"},
        "itemEndDate": iso(utcnow() + timedelta(days=2)),
    }


def test_hybrid_listing_is_bin_in_fixed_price_pass_and_auction_in_auction_pass():
    collector = EbayCollector(Database(":memory:"))
    fixed = collector._parse_item(_raw(), "category:27386", auction=False)
    assert fixed is not None
    assert fixed.is_auction is False
    # The Buy It Now price is what the buyer must pay to secure the item.
    assert fixed.price == 49.99
    assert fixed.shipping == 4.50

    auction = collector._parse_item(_raw(), "auction-category:Graphics", auction=True)
    assert auction is not None
    assert auction.is_auction is True
    assert auction.price == 12.00
    assert auction.current_bid == 12.00


def test_item_detail_description_is_used_for_condition():
    collector = EbayCollector(Database(":memory:"))
    raw = _raw()
    raw["title"] = "Corsair Vengeance 8GB DDR4 RAM"
    raw["description"] = (
        "Spares or repairs. I cannot test it because my new PC only takes DDR5."
    )
    raw["condition"] = "For parts or not working"
    item = collector._parse_item(raw, "item-detail", auction=False)
    assert item is not None
    assert "cannot test" in item.description
    assert "For parts or not working" not in item.description
    identified = identify_listing(item.title, item.description)
    assert identified.attrs.get("functionality") == "untested"
    assert identified.is_faulty is False


def test_spares_or_repairs_untested_is_not_confirmed_faulty():
    result = identify_listing(
        "Corsair Vengeance 8GB DDR4 RAM - spares or repairs",
        "Untested because my new PC only takes DDR5, so I cannot test it.",
    )
    assert result.product_key == "ram:ddr4:8gb"
    assert result.is_faulty is False
    assert result.attrs.get("functionality") == "untested"
    assert "untested_condition" in result.flags

    faulty = identify_listing(
        "Corsair Vengeance 8GB DDR4 RAM faulty",
        "Not working, no display on boot.",
    )
    assert faulty.is_faulty is True
    assert faulty.attrs.get("functionality") == "faulty"


def test_generic_ebay_condition_label_is_not_seller_fault_evidence():
    # Search summaries used to store "For parts or not working" as the
    # description. That is eBay's coarse label, not a statement that the RAM
    # has failed, and it must not turn an untested module into scrap.
    result = identify_listing(
        "Corsair Vengeance 8GB DDR4 RAM - Untested",
        "For parts or not working",
    )
    assert result.product_key == "ram:ddr4:8gb"
    assert result.is_faulty is False
    assert result.attrs.get("functionality") == "untested"

    # A seller-written failure is still a fault, even with the same label.
    faulty = identify_listing(
        "Corsair Vengeance 8GB DDR4 RAM",
        "For parts or not working. No display on boot.",
    )
    assert faulty.is_faulty is True
    assert faulty.attrs.get("functionality") == "faulty"


def test_non_ecc_and_ddr_speed_are_identified_exactly():
    result = identify_listing(
        "Corsair Vengeance LPX 16GB (2x8GB) DDR4-3000 DIMM Non-ECC RAM"
    )
    assert result.product_key == "ram:ddr4:16gb:3000:2x8:desktop"
    assert result.attrs["ecc"] is False
    assert result.attrs["speed_mhz"] == 3000
    assert result.attrs["form_factor"] == "desktop"

    ecc = identify_listing("Samsung 16GB DDR4 ECC Registered Server RAM")
    assert ecc.attrs["ecc"] is True
    assert ecc.product_key.endswith(":ecc")

    laptop = identify_listing(
        "Corsair Vengeance 8GB DDR4-2933 SO-DIMM 260-Pin Non-ECC RAM"
    )
    assert laptop.product_key == "ram:ddr4:8gb:2933:laptop"
    assert laptop.attrs["form_factor"] == "laptop"

    candidate = variant_from_title(
        "Corsair Vengeance LPX 16GB (2x8GB) DDR4-3000 DIMM Non-ECC RAM"
    )
    same = variant_from_title(
        "Corsair Vengeance LPX 16GB (2x8GB) DDR4-3000 DIMM RAM"
    )
    assert compare_variants(candidate, same).status == "exact"
    ecc_comp = variant_from_title("16GB DDR4-3000 ECC Registered Server RAM")
    match = compare_variants(candidate, ecc_comp)
    assert match.status == "incompatible"
    assert match.field == "ecc"


def test_laptop_ram_is_not_a_portable_device_but_a_laptop_is():
    accepted = comparable_rejection(
        {"title": "8GB DDR4-3200 SO-DIMM Laptop RAM 260-Pin Unbuffered", "source": "ebay"},
        kind="ram",
        product_key="ram:ddr4:8gb:laptop",
    )
    assert accepted is None

    rejected = comparable_rejection(
        {"title": "Dell Latitude 7420 Laptop 16GB RAM 512GB SSD", "source": "ebay"},
        kind="ram",
        product_key="ram:ddr4:8gb:laptop",
    )
    assert rejected == "complete system, not a standalone component"


def test_auctions_do_not_enter_the_human_review_queue():
    db = Database(":memory:")
    save_scan_config(
        db,
        categories=["pc-components/graphics-cards"],
        min_price=0,
        max_price=150,
        marketplaces={"vinted": True, "gumtree": True, "ebay": True},
        active_preset="test",
        confirmed=True,
    )
    listing_id = db.execute(
        """
        INSERT INTO listings(
            marketplace, external_id, url, title, price, currency, condition_norm,
            listing_kind, category, product_key, product_name, is_auction,                auction_end_at, current_bid, first_seen, last_seen, status, scan_leaf,
                created_at, updated_at
            ) VALUES ('ebay','review-auction','https://www.ebay.co.uk/itm/2',
                      'NVIDIA GeForce RTX 4070 12GB Graphics Card', 80, 'GBP', 'used',
                      'gpu','gpu','gpu:rtx-4070-12gb','NVIDIA GeForce RTX 4070 12GB',1,
                      ?, 80, ?, ?, 'active','pc-components/graphics-cards', ?, ?)
            """,
            (
                iso(utcnow() + timedelta(days=2)),
                iso(), iso(), iso(), iso(),
            ),
    ).lastrowid
    db.execute(
        """            INSERT INTO deals(listing_id, product_key, kind, expected_resale, confidence,
                purchase_total, net_profit, roi, is_deal, needs_review, eligibility,
                computed_at)
            VALUES (?, 'gpu:rtx-4070-12gb', 'gpu', 220, 0.7, 84.5, 100, 1.2, 0, 1,
                    'needs_verification', ?)
        """,
        (listing_id, iso()),
    )

    with TestClient(create_app(db, autostart=False)) as client:
        review = client.get("/api/deals", params={"review": 1}).json()
        assert review["total"] == 0
        detail = client.get(f"/api/deals/{listing_id}").json()
        assert detail["is_auction"] is True
        assert detail["eligibility"] == "needs_verification"


def test_ebay_price_bands_cover_the_budget_and_narrow_the_filter():
    db = Database(":memory:")
    save_scan_config(
        db,
        categories=["pc-components/memory"],
        min_price=0,
        max_price=150,
        marketplaces={"vinted": True, "gumtree": True, "ebay": True},
        active_preset="test",
        confirmed=True,
    )
    scanner = Scanner(db, collectors={})
    bands = scanner.ebay_price_bands()
    assert len(bands) >= 5
    assert bands[0][0] == 1.0
    assert bands[-1][1] == 150.0
    for (low, high), (next_low, _next_high) in zip(bands, bands[1:]):
        assert high < next_low
        assert low < high

    collector = EbayCollector(db)
    collector.scan_price_min, collector.scan_price_max = 0, 150
    filtered = collector._browse_filter(75, 100)
    assert "buyingOptions:{FIXED_PRICE}" in filtered
    # Without priceCurrency eBay silently ignores the numeric range and
    # returns items far above the saved budget.
    assert "priceCurrency:GBP" in filtered
    assert "price:[75..100]" in filtered

    # A short page must not end discovery while the provider reports more.
    collector._last_total = 5000
    async def fake_request(_params, _label, auction=False):
        return [], 150, 200
    collector._browse_request = fake_request  # type: ignore[method-assign]
    from types import SimpleNamespace
    page = asyncio.run(
        collector.category_page(SimpleNamespace(category_id="27386"), 1)
    )
    assert page.total == 5000
    assert page.has_more is True


def test_enabled_storage_and_network_toggles_use_their_real_leaf_categories():
    from app.scan_taxonomy import category_to_leaf, mapped_category_ids

    db = Database(":memory:")
    save_scan_config(
        db,
        categories=[
            "pc-components/ssds",
            "pc-components/hdds",
            "pc-components/network-adapters",
        ],
        min_price=0,
        max_price=100,
        marketplaces={"vinted": True, "gumtree": True, "ebay": True},
        active_preset="test",
        confirmed=True,
    )
    config = load_scan_config(db)
    ids = set(mapped_category_ids("ebay", config.categories))
    assert "175669" in ids  # Solid State Drives, not the mixed parent
    assert "56083" in ids  # Internal Hard Disk Drives
    assert "20318" in ids  # Network Cards
    assert "45002" in ids  # USB Wi-Fi Adapters/Dongles
    assert "182085" not in ids  # the broad HDD/SSD/NAS parent is no longer used
    assert "pc-components/ssds" in category_to_leaf("ebay", "175669")
    assert "pc-components/network-adapters" in category_to_leaf("ebay", "20318")


def test_ebay_budget_uses_more_than_the_old_four_calls_per_cycle():
    db = Database(":memory:")
    scanner = Scanner(db, collectors={})
    plan = scanner.ebay_budget_snapshot()
    assert plan["daily_limit"] == 5000
    assert plan["auction_reserve"] >= 1000
    assert plan["budget"] > 4
    assert plan["budget"] <= 120
    # A manual pass can cover every enabled category, but still stops at the
    # daily allowance after the auction reserve.
    manual = scanner.ebay_budget_snapshot(manual=True)
    assert 4 < manual["budget"] <= 200
    assert manual["budget"] <= plan["remaining_after_reserve"] or plan["remaining_after_reserve"] < 200
