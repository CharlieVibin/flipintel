"""Structured variant regressions and the valuation pipeline.

These tests exercise the same path as a live refresh: a listing is identified,
its comparable pool is filtered, prices are normalised, a resale estimate is
produced and the promotion gate is applied. A title-similarity score is never
the deciding factor.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.comparables import split_pool
from app.db import Database
from app.identification import identify_listing
from app.notifications import Notifier
from app.utils import iso
from app.valuation import (
    ValuationConfig,
    ValuationEngine,
    _estimate_from_rows,
    _provisional_evidence_ok,
)


def _candidate(title: str) -> dict:
    ident = identify_listing(title)
    return {
        "title": title,
        "product_key": ident.product_key,
        "product_name": ident.product_name,
        "listing_kind": ident.kind,
        "attrs": json.dumps(ident.attrs),
    }


def _comp(title: str, price: float = 400.0, marketplace: str = "ebay") -> dict:
    return {
        "title": title,
        "price": price,
        "marketplace": marketplace,
        "source": marketplace,
        "source_id": f"test:{abs(hash(title))}",
        "observed_at": iso(),
        "condition_norm": "good",
        "is_sold": 0,
    }


def _verdict(candidate_title: str, comparable_title: str) -> tuple[list[dict], list[dict]]:
    candidate = _candidate(candidate_title)
    assert candidate["product_key"], candidate_title
    return split_pool(
        [_comp(comparable_title)],
        kind=candidate["listing_kind"],
        product_key=str(candidate["product_key"]),
        candidate=candidate,
    )


def test_steam_deck_identity_distinguishes_lcd_oled_and_storage():
    target = identify_listing("Steam deck 1tb lcd")
    assert target.product_key == "console:steam-deck-lcd:1tb:upgraded"
    assert target.attrs["display"] == "lcd"
    assert target.attrs["storage_gb"] == 1024
    assert target.attrs["storage_origin"] == "upgraded"
    assert "variant_unclear" not in target.flags
    assert target.identity_level == "strong"

    oled = identify_listing("Valve Steam Deck 512GB OLED Handheld Console - Black")
    assert oled.product_key == "console:steam-deck-oled:512gb"
    assert oled.attrs["display"] == "oled"
    assert oled.attrs["storage_gb"] == 512

    oled_1tb = identify_listing("Valve Steam Deck 1TB OLED Handheld Console 100% battery health")
    assert oled_1tb.product_key == "console:steam-deck-oled:1tb"

    factory_lcd = identify_listing("Valve Steam Deck LCD 512GB")
    assert factory_lcd.product_key == "console:steam-deck-lcd:512gb"
    assert factory_lcd.attrs["storage_origin"] == "factory"

    unknown = identify_listing("Steam Deck 512GB")
    assert unknown.product_key == "console:steam-deck:512gb"
    assert "variant_unclear" in unknown.flags


def test_steam_deck_oled_cannot_enter_an_lcd_comparable_pool():
    accepted, rejected = split_pool(
        [
            _comp("Valve Steam Deck 512GB OLED Handheld Console - Black", 445.30),
            _comp("Valve Steam Deck 1TB OLED Handheld Console 100% battery health", 577.90),
            _comp("Steam Deck 1TB OLED Handheld Gaming Console", 547.30),
        ],
        kind="console",
        product_key="console:steam-deck-lcd:1tb:upgraded",
        candidate=_candidate("Steam deck 1tb lcd"),
    )
    assert accepted == []
    assert len(rejected) == 3
    assert all(row["conflict"] in {"display", "storage", "capacity"} for row in rejected)
    assert any("LCD" in row["reason"] and "OLED" in row["reason"] for row in rejected)

    accepted, rejected = split_pool(
        [_comp("Steam Deck LCD 1TB upgraded SSD", 410.0)],
        kind="console",
        product_key="console:steam-deck-lcd:1tb:upgraded",
        candidate=_candidate("Steam deck 1tb lcd"),
    )
    assert rejected == []
    assert accepted and accepted[0]["match_quality"] == "exact"


def test_steam_deck_lcd_pipeline_ignores_miskeyed_oled_comparables(tmp_path):
    db = Database(tmp_path / "steam-deck-variants.db")
    try:
        now = iso()
        def insert(title: str, price: float, key: str, ident) -> int:
            return db.execute(
                """
                INSERT INTO listings(
                    marketplace, external_id, url, title, price, condition_norm,
                    listing_kind, category, product_key, product_name, attrs, flags,
                    identification_confidence, identity_level, first_seen, last_seen,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'good', ?, 'console', ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    "ebay", f"id-{abs(hash(title))}", f"https://www.ebay.co.uk/itm/{abs(hash(title))}",
                    title, price, ident.kind, key, ident.product_name,
                    json.dumps(ident.attrs), json.dumps(ident.flags),
                    ident.confidence, ident.identity_level, now, now, now, now,
                ),
            ).lastrowid

        candidate = identify_listing("Steam deck 1tb lcd")
        candidate_id = insert(
            "Steam deck 1tb lcd", 180.0,
            "console:steam-deck-lcd:1tb:upgraded", candidate,
        )
        # Simulate the old contamination: OLED listings filed under the LCD key.
        # The validator must reject them even though the SQL pool returns them.
        for title in (
            "Valve Steam Deck 512GB OLED Handheld Console - Black",
            "Valve Steam Deck 1TB OLED Handheld Console 100% battery health",
            "Steam Deck 1TB OLED Handheld Gaming Console",
            "Steam deck 512GB OLED",
            "Oled 1TB steam deck with accessories",
            "Steam Deck OLED 512GB",
        ):
            ident = identify_listing(title)
            insert(title, 450.0, "console:steam-deck-lcd:1tb:upgraded", ident)

        engine = ValuationEngine(db)
        engine.refresh(full=True)
        deal = db.query_one("SELECT * FROM deals WHERE listing_id=?", (candidate_id,))
        assert deal is not None
        assert deal["expected_resale"] is None
        assert deal["is_deal"] == 0
        assert deal["sample_size"] == 0
        breakdown = json.loads(deal["breakdown"])
        assert breakdown["comps"] == []
        assert len(breakdown["rejected_comps"]) >= 3

        # Add genuine LCD 1TB upgraded evidence. It must set a defensible
        # estimate and every displayed accepted comparable must contribute.
        for index in range(6):
            title = f"Steam Deck LCD 1TB upgraded SSD {index}"
            ident = identify_listing(title)
            insert(title, 380.0 + index * 12, "console:steam-deck-lcd:1tb:upgraded", ident)
        engine.refresh(full=True)
        deal = db.query_one("SELECT * FROM deals WHERE listing_id=?", (candidate_id,))
        breakdown = json.loads(deal["breakdown"])
        assert deal["expected_resale"] is not None
        assert deal["expected_resale"] < 450
        assert breakdown["comps"]
        assert all("OLED" not in str(comp["title"]).upper() for comp in breakdown["comps"])
        assert all(comp["contributes"] is True for comp in breakdown["comps"])
        assert all(comp["match_quality"] == "exact" for comp in breakdown["comps"])
        assert breakdown["context_comps"] == []
    finally:
        db.close()


@pytest.mark.parametrize(
    ("candidate", "comparable"),
    [
        ("RTX 4070 12GB", "RTX 4070 Ti 12GB"),
        ("RTX 4070 12GB", "RTX 4070 Super 12GB"),
        ("RTX 4070 Ti 12GB", "RTX 4070 Ti Super 12GB"),
        ("RTX 4060 8GB", "RTX 4060 Ti 8GB"),
        ("RTX 3060 8GB", "RTX 3060 12GB"),
        ("RX 7600 8GB", "RX 7600 XT 8GB"),
        ("RX 7900 XT 20GB", "RX 7900 XTX 24GB"),
    ],
)
def test_gpu_suffix_and_vram_conflicts_are_rejected(candidate, comparable):
    accepted, rejected = _verdict(candidate, comparable)
    assert accepted == []
    assert rejected and rejected[0]["match_quality"] == "incompatible"


def test_gpu_laptop_is_not_a_desktop_comparable():
    accepted, rejected = _verdict("RTX 4070 12GB desktop graphics card", "RTX 4070 12GB laptop GPU")
    assert accepted == []
    assert rejected


@pytest.mark.parametrize(
    ("candidate", "comparable"),
    [
        ("PS5 Disc Edition 825GB", "PS5 Digital Edition 825GB"),
        ("PS5 Slim Disc 1TB", "PS5 Slim Digital 1TB"),
        ("PS5 Pro 2TB", "PS5 Slim Disc 1TB"),
        ("Xbox Series S 512GB", "Xbox Series X 1TB"),
        ("Nintendo Switch", "Nintendo Switch OLED"),
        ("Nintendo Switch", "Nintendo Switch 2"),
    ],
)
def test_console_revisions_and_editions_are_rejected(candidate, comparable):
    accepted, rejected = _verdict(candidate, comparable)
    assert accepted == []
    assert rejected and rejected[0]["match_quality"] == "incompatible"


@pytest.mark.parametrize(
    ("candidate", "comparable"),
    [
        ("iPhone 15 Pro 128GB", "iPhone 15 Pro 256GB"),
        ("iPhone 15 Pro 128GB", "iPhone 15 Pro Max 128GB"),
        ("Samsung Galaxy S24 256GB", "Samsung Galaxy S24 Ultra 256GB"),
    ],
)
def test_phone_variants_are_rejected(candidate, comparable):
    accepted, rejected = _verdict(candidate, comparable)
    assert accepted == []
    assert rejected and rejected[0]["match_quality"] == "incompatible"


@pytest.mark.parametrize(
    ("candidate", "comparable"),
    [
        ("Corsair 32GB DDR5 6000MHz (2x16GB)", "Corsair 32GB DDR5 6000MHz (4x8GB)"),
        ("Corsair 32GB DDR4 3200MHz", "Corsair 32GB DDR5 3200MHz"),
        ("Kingston 16GB DDR4 SODIMM laptop RAM", "Kingston 16GB DDR4 3200MHz desktop DIMM"),
        ("Samsung 32GB DDR4 ECC registered RAM", "Samsung 32GB DDR4 3200MHz desktop RAM"),
        ("Corsair 32GB DDR5 6000MHz", "Corsair 64GB DDR5 6000MHz"),
    ],
)
def test_memory_generation_form_and_kit_conflicts_are_rejected(candidate, comparable):
    accepted, rejected = _verdict(candidate, comparable)
    assert accepted == []
    assert rejected and rejected[0]["match_quality"] == "incompatible"


def test_complete_system_faulty_and_refurbished_are_not_working_used_comparables():
    accepted, rejected = _verdict(
        "Intel Core i5-12400 CPU", "Dell Optiplex 7000 i5-12400 16GB RAM 512GB SSD desktop PC"
    )
    assert accepted == []
    assert rejected and "complete system" in rejected[0]["reason"]

    accepted, rejected = _verdict(
        "RTX 4060 8GB graphics card", "RTX 4060 8GB faulty no display"
    )
    assert accepted == []
    assert rejected

    accepted, rejected = _verdict(
        "PlayStation 5 Disc Edition console", "PlayStation 5 Disc Edition refurbished console"
    )
    assert accepted == []
    assert rejected and "refurbished" in rejected[0]["reason"]


def test_provisional_evidence_gate_allows_a_small_uncertain_minority():
    assert _provisional_evidence_ok(97, 3) is True
    assert _provisional_evidence_ok(36, 3) is True
    assert _provisional_evidence_ok(133, 65) is False
    assert _provisional_evidence_ok(1, 0) is False
    assert _provisional_evidence_ok(4, 2) is False


def test_unknown_storage_is_accepted_only_as_provisional_evidence():
    candidate = _candidate("iPhone 15 128GB")
    accepted, rejected = split_pool(
        [_comp("Apple iPhone 15", 500.0)],
        kind="phone",
        product_key=str(candidate["product_key"]),
        candidate=candidate,
    )
    assert rejected == []
    assert accepted and accepted[0]["match_quality"] == "uncertain"
    assert "storage" in accepted[0]["match_reason"]


def test_estimate_separates_contributing_comps_from_context_and_cex():
    rows = [
        {**_comp("Steam Deck LCD 1TB upgraded SSD 1", 400.0), "condition_norm": "good"},
        {**_comp("Steam Deck LCD 1TB upgraded SSD 2", 420.0), "condition_norm": "good"},
        {**_comp("Steam Deck LCD 1TB upgraded SSD 3", 430.0), "condition_norm": "good"},
        {**_comp("Steam Deck LCD 1TB upgraded SSD 4", 440.0), "condition_norm": "good"},
        {**_comp("Steam Deck LCD 512GB", 320.0), "condition_norm": "good"},
        {
            "title": "CeX retail quote",
            "price": 500.0,
            "marketplace": "cex",
            "source": "cex",
            "source_id": "cex:console:steam-deck-lcd:1tb:upgraded",
            "condition_norm": "good",
            "is_sold": 0,
            "observed_at": iso(),
            "cex_cash": 300.0,
            "cex_voucher": 350.0,
        },
    ]
    estimate = _estimate_from_rows(
        rows, ValuationConfig(), 0.9, "good", [], "console"
    )
    assert estimate.resale_good
    assert estimate.basis_size
    assert estimate.cex_reference
    assert estimate.cex_reference["retail"] == 500.0
    assert all(comp["contributes"] is True for comp in estimate.comps)
    assert all(comp["contributes"] is False for comp in estimate.context_comps)
    assert estimate.cex_reference["used_in_estimate"] is False


@pytest.mark.asyncio
async def test_incompatible_variants_never_promote_or_notify(tmp_path, monkeypatch):
    db = Database(tmp_path / "notification-gate-negative.db")
    try:
        now = iso()
        candidate = identify_listing("Steam deck 1tb lcd")
        candidate_id = db.execute(
            """
            INSERT INTO listings(
                marketplace, external_id, url, title, price, condition_norm,
                listing_kind, category, product_key, product_name, attrs, flags,
                identification_confidence, identity_level, first_seen, last_seen,
                status, created_at, updated_at
            ) VALUES ('gumtree', 'lcd-negative', 'https://www.gumtree.com/p/lcd-negative', ?, 180.0,
                      'good', ?, 'console', ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                "Steam deck 1tb lcd", candidate.kind,
                "console:steam-deck-lcd:1tb:upgraded", candidate.product_name,
                json.dumps(candidate.attrs), json.dumps(candidate.flags),
                candidate.confidence, candidate.identity_level, now, now, now, now,
            ),
        ).lastrowid
        for index, title in enumerate(
            (
                "Valve Steam Deck 512GB OLED Handheld Console - Black",
                "Valve Steam Deck 1TB OLED Handheld Console 100% battery health",
                "Steam Deck OLED 1TB Handheld Gaming Console",
            )
        ):
            db.execute(
                """
                INSERT INTO listings(
                    marketplace, external_id, url, title, price, condition_norm,
                    listing_kind, category, product_key, product_name, attrs, flags,
                    identification_confidence, identity_level, first_seen, last_seen,
                    status, created_at, updated_at
                ) VALUES ('ebay', ?, ?, ?, 450.0, 'good', 'console', 'console',
                          'console:steam-deck-lcd:1tb:upgraded', 'Steam Deck LCD 1TB',
                          '{}', '[]', 0.9, 'strong', ?, ?, 'active', ?, ?)
                """,
                (
                    f"oled-{index}", f"https://www.ebay.co.uk/itm/oled-{index}", title,
                    now, now, now, now,
                ),
            )
        db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES ('notifications_enabled','true',?) "
            "ON CONFLICT(key) DO UPDATE SET value='true', updated_at=excluded.updated_at",
            (now,),
        )
        ValuationEngine(db).refresh(full=True)
        deal = db.query_one("SELECT is_deal, expected_resale FROM deals WHERE listing_id=?", (candidate_id,))
        assert deal is not None
        assert deal["is_deal"] == 0
        assert deal["expected_resale"] is None

        notifier = Notifier(db)
        monkeypatch.setattr(notifier, "_webhook", AsyncMock(return_value="https://discord.com/api/webhooks/test"))
        monkeypatch.setattr(notifier, "_send", AsyncMock(return_value=True))
        assert await notifier.notify_new_deals() == 0
        notifier._send.assert_not_awaited()
        await notifier.close()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_promoted_deal_requires_exact_variants_and_can_notify_discord(
    tmp_path, monkeypatch
):
    db = Database(tmp_path / "notification-gate.db")
    try:
        now = iso()
        def insert(title: str, price: float, key: str, ident) -> int:
            return db.execute(
                """
                INSERT INTO listings(
                    marketplace, external_id, url, title, price, condition_norm,
                    listing_kind, category, product_key, product_name, attrs, flags,
                    identification_confidence, identity_level, first_seen, last_seen,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'good', ?, 'console', ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    "gumtree", f"notify-{abs(hash(title))}", f"https://www.gumtree.com/p/{abs(hash(title))}",
                    title, price, ident.kind, key, ident.product_name,
                    json.dumps(ident.attrs), json.dumps(ident.flags),
                    ident.confidence, ident.identity_level, now, now, now, now,
                ),
            ).lastrowid

        candidate = identify_listing("Steam deck 1tb lcd")
        candidate_id = insert(
            "Steam deck 1tb lcd", 255.0,
            "console:steam-deck-lcd:1tb:upgraded", candidate,
        )
        for index in range(7):
            title = f"Steam Deck LCD 1TB upgraded SSD {index}"
            ident = identify_listing(title)
            insert(title, 400.0 + index * 10, "console:steam-deck-lcd:1tb:upgraded", ident)

        db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES ('notifications_enabled','true',?) "
            "ON CONFLICT(key) DO UPDATE SET value='true', updated_at=excluded.updated_at",
            (now,),
        )
        engine = ValuationEngine(db)
        engine.refresh(full=True)
        deal = db.query_one("SELECT is_deal, expected_resale FROM deals WHERE listing_id=?", (candidate_id,))
        assert deal["is_deal"] == 1, deal

        notifier = Notifier(db)
        monkeypatch.setattr(notifier, "_webhook", AsyncMock(return_value="https://discord.com/api/webhooks/test"))
        monkeypatch.setattr(notifier, "_send", AsyncMock(return_value=True))
        sent = await notifier.notify_new_deals()
        assert sent == 1
        notifier._send.assert_awaited_once()
        assert notifier._send.await_args.kwargs["listing_id"] == candidate_id
        await notifier.close()
    finally:
        db.close()
