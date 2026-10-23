"""Valuation-integrity regressions seen in the live database.

These tests use explicit comparable rows rather than the classifier so each
failure mode is isolated: new stock leaking into a used estimate, one
marketplace dominating the pool, duplicated offers counted as independent
evidence, and a range that contradicted its own central value.
"""
from __future__ import annotations

from app.identification import identify_listing
from app.utils import iso
from app.valuation import ValuationConfig, _estimate_from_rows


def _rows(entries: list[tuple[str, float, str, str]]) -> list[dict]:
    return [
        {
            "title": title,
            "price": price,
            "condition_norm": condition,
            "marketplace": marketplace,
            "source": marketplace,
            "source_id": f"{marketplace}:{index}",
            "observed_at": iso(),
            "is_sold": 0,
        }
        for index, (marketplace, price, condition, title) in enumerate(entries)
    ]


def test_new_stock_does_not_set_the_used_price():
    entries: list[tuple[str, float, str, str]] = []
    for index in range(6):
        entries.append(("gumtree", 300 + index * 5, "good", f"RTX 4060 8GB used {index}"))
    for index in range(4):
        entries.append(
            ("ebay", 900 + index * 20, "new", f"RTX 4060 8GB brand new sealed {index}")
        )
    estimate = _estimate_from_rows(
        _rows(entries), ValuationConfig(), 0.8, "good", [], "gpu"
    )
    assert estimate.resale_good is not None
    assert estimate.resale_good < 400, estimate.resale_good
    assert any("new-condition" in note for note in estimate.notes)


def test_new_estimate_is_bounded_by_its_range():
    entries: list[tuple[str, float, str, str]] = [
        ("vinted", 420, "very_good", "RTX 4070 12GB"),
        ("vinted", 450, "very_good", "RTX 4070 12GB"),
        ("gumtree", 430, "good", "RTX 4070 12GB"),
        ("gumtree", 470, "good", "RTX 4070 12GB"),
        ("ebay", 520, "used", "RTX 4070 12GB"),
        ("ebay", 890, "new", "RTX 4070 12GB new"),
    ]
    for condition in ("good", "new", "used"):
        estimate = _estimate_from_rows(
            _rows(entries), ValuationConfig(), 0.8, condition, [], "gpu"
        )
        assert estimate.resale_good is not None
        assert estimate.low is not None and estimate.high is not None
        assert estimate.low <= estimate.resale_good <= estimate.high, (
            condition,
            estimate.low,
            estimate.resale_good,
            estimate.high,
        )


def test_one_marketplace_cannot_set_the_asking_market():
    entries: list[tuple[str, float, str, str]] = [
        ("ebay", 600, "used", "PlayStation 5 Pro console") for _ in range(12)
    ]
    entries += [
        ("gumtree", 300, "good", "PlayStation 5 Pro console") for _ in range(4)
    ]
    entries += [
        ("vinted", 320, "very_good", "PlayStation 5 Pro console") for _ in range(2)
    ]
    estimate = _estimate_from_rows(
        _rows(entries), ValuationConfig(), 0.85, "good", [], "console"
    )
    assert estimate.resale_good is not None
    # The eBay asks are 600 and carry most of the raw weight. Capping each
    # marketplace keeps the smaller private asks in the estimate.
    assert estimate.resale_good < 480, estimate.resale_good


def test_identical_repeated_offers_count_once():
    duplicate_entries: list[tuple[str, float, str, str]] = [
        ("vinted", 300, "very_good", "RTX 4060 8GB MSI Ventus") for _ in range(6)
    ]
    duplicate_entries += [
        ("gumtree", 280, "good", "RTX 4060 8GB"),
        ("gumtree", 310, "good", "RTX 4060 8GB"),
    ]
    estimate = _estimate_from_rows(
        _rows(duplicate_entries), ValuationConfig(), 0.8, "good", [], "gpu"
    )
    assert estimate.sample_size <= 3, estimate.sample_size


def test_console_covers_and_games_are_not_console_hardware():
    covers = identify_listing(
        "Official Sony PlayStation 5 Pro Console Covers Limited Edition Ghost of Yotei"
    )
    assert covers.product_key is None
    assert "accessory_or_box" in covers.flags

    game = identify_listing(
        "IO Interactive 007 First Light PS5 Pro Enhanced PEGI 16 James Bond 007"
    )
    assert game.product_key is None
    assert "console_game" in game.flags


def test_console_bundle_extras_are_flagged():
    for title in (
        "PlayStation 5 Pro 2TB + Disc Drive with Dualsense Edge controller",
        "Sony PlayStation 5 Pro Console White/Black w 2 DualSense, Headset & Cables",
        "Xbox Series X 1TB Console with 2 controllers",
    ):
        result = identify_listing(title)
        assert result.is_bundle is True, (title, result.flags)
    # One included controller is normal, not a bundle.
    single = identify_listing("Sony PS5 Slim Disc Console with Controller")
    assert single.is_bundle is False, single.flags
    assert single.product_key == "console:ps5-slim"
