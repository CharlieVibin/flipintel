"""Stored listings must obey the active preset at the query level.

These regressions cover the reported failure: the scanner stopped searching a
category or a price band, but old rows kept appearing in the dashboard and in
Discord alerts. Nothing here deletes history; history mode only labels it.
"""
from __future__ import annotations

import json
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from app.notifications import Notifier
from app.scan_config import save_scan_config
from app.utils import iso, utcnow
from app.visibility import backfill_scan_leafs

GPU = "pc-components/graphics-cards"
RAM = "pc-components/memory"
MICE = "peripherals/mice"


def _save_config(db: Database, categories: list[str], *, max_price: float = 100,
                 min_price: float = 0, marketplaces: dict[str, bool] | None = None):
    return save_scan_config(
        db,
        categories=categories,
        min_price=min_price,
        max_price=max_price,
        marketplaces=marketplaces or {"vinted": True, "gumtree": True, "ebay": True},
        active_preset="test-preset",
        confirmed=True,
    )


def _listing(
    db: Database,
    external_id: str,
    title: str,
    price: float,
    *,
    marketplace: str = "ebay",
    shipping: float = 0,
    category: str = "gpu",
    product_key: str | None = "gpu:nvidia:rtx-3060-12gb",
    product_name: str = "NVIDIA GeForce RTX 3060 12GB",
    kind: str = "gpu",
    is_auction: bool = False,
    current_bid: float | None = None,
    ends_in_minutes: int | None = None,
    faulty: bool = False,
    first_seen: str | None = None,
) -> int:
    now = first_seen or iso()
    end = iso(utcnow() + timedelta(minutes=ends_in_minutes)) if ends_in_minutes else None
    return int(
        db.execute(
            """
            INSERT INTO listings(
                marketplace, external_id, url, title, price, shipping, condition_norm,
                listing_kind, category, product_key, product_name,
                identification_confidence, flags, attrs, is_faulty, is_auction,
                auction_end_at, current_bid, bid_count, min_next_bid,
                first_seen, last_seen, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'good', ?, ?, ?, ?, 0.9, '[]', '{}', ?, ?, ?, ?,
                      1, ?, ?, ?, 'active', ?, ?)
            """,
            (
                marketplace, external_id, f"https://www.{marketplace}.co.uk/itm/{external_id}",
                title, price, shipping, kind, category, product_key, product_name,
                int(faulty), int(is_auction), end,
                current_bid if current_bid is not None else (price if is_auction else None),
                None, now, now, now, now,
            ),
        ).lastrowid
    )


def _deal(
    db: Database,
    listing_id: int,
    *,
    expected_resale: float | None = 160,
    net_profit: float = 40,
    purchase_total: float = 80,
    is_deal: bool = True,
    evidence: str = "strong",
    max_bid: float | None = 110,
    needs_review: bool = False,
    product_key: str = "gpu:nvidia:rtx-3060-12gb",
    kind: str = "gpu",
) -> None:
    db.execute(
        """
        INSERT INTO deals(
            listing_id, product_key, kind, expected_resale, confidence, sample_size,
            valuation_method, purchase_total, net_profit, roi, score, is_deal,
            needs_review, evidence_level, max_bid, computed_at
        ) VALUES (?, ?, ?, ?, 0.7, 8, 'sold_evidence', ?, ?, ?, 1.0, ?, ?, ?, ?, ?)
        """,
        (
            listing_id, product_key, kind, expected_resale, purchase_total,
            net_profit, (net_profit / purchase_total if purchase_total else 0),
            int(is_deal), int(needs_review), evidence, max_bid, iso(),
        ),
    )


def _gpu(db: Database, ext: str, title: str, price: float, **kwargs) -> int:
    return _listing(db, ext, title, price, **kwargs)


def _seed_feed(db: Database) -> None:
    _save_config(db, [GPU, RAM, MICE])
    now = iso()
    visible_gpu = _gpu(db, "gpu-1", "NVIDIA GeForce RTX 3060 12GB Graphics Card", 80)
    _deal(db, visible_gpu)
    over_budget = _gpu(db, "gpu-2", "NVIDIA GeForce RTX 4070 Graphics Card", 400)
    _deal(db, over_budget, expected_resale=500, net_profit=90, purchase_total=400)
    pc = _listing(
        db, "pc-1", "Gaming PC Ryzen 5 5600X RTX 3060 16GB", 250,
        category="pc", product_key="pc:gaming:ryzen-5-5600x-rtx-3060",
        product_name="Gaming PC Ryzen 5 5600X RTX 3060", kind="pc",
    )
    _deal(db, pc, expected_resale=350, net_profit=60, purchase_total=250,
          product_key="pc:gaming:ryzen-5-5600x-rtx-3060", kind="pc")
    switch = _listing(
        db, "console-1", "Nintendo Switch OLED Console", 150,
        category="console", product_key="console:switch-oled",
        product_name="Nintendo Switch OLED", kind="console",
    )
    _deal(db, switch, expected_resale=220, net_profit=50, purchase_total=150,
          product_key="console:switch-oled", kind="console")
    vinted_ram = _listing(
        db, "ram-1", "Corsair Vengeance DDR4 16GB RAM Kit", 75, marketplace="vinted",
        category="ram", product_key="ram:ddr4:16gb",
        product_name="Corsair Vengeance DDR4 16GB", kind="ram",
    )
    _deal(db, vinted_ram, expected_resale=110, net_profit=25, purchase_total=82.94,
          product_key="ram:ddr4:16gb", kind="ram")
    gumtree_ram = _listing(
        db, "ram-2", "Crucial DDR5 32GB RAM Kit", 99.50, marketplace="gumtree",
        shipping=10, category="ram", product_key="ram:ddr5:32gb",
        product_name="Crucial DDR5 32GB", kind="ram",
    )
    _deal(db, gumtree_ram, expected_resale=150, net_profit=35, purchase_total=109.50,
          product_key="ram:ddr5:32gb", kind="ram")
    phone = _listing(
        db, "phone-1", "Apple iPhone 13 128GB Unlocked", 50,
        category="phone", product_key="phone:iphone-13:128gb",
        product_name="Apple iPhone 13 128GB", kind="phone",
    )
    _deal(db, phone, expected_resale=260, net_profit=180, purchase_total=50,
          product_key="phone:iphone-13:128gb", kind="phone")
    backfill_scan_leafs(db)
    assert now


def test_deal_feed_applies_category_marketplace_and_total_cost():
    db = Database(":memory:")
    _seed_feed(db)
    with TestClient(create_app(db, autostart=False)) as client:
        response = client.get("/api/deals").json()
        assert response["total"] == 2
        ids = {deal["external_id"] for deal in response["deals"]}
        assert ids == {"gpu-1", "ram-1"}
        # Total cost, not item price, controls the Vinted row.
        assert response["preset"]["max_price"] == 100
        assert response["hidden_by_preset"] == 5

        history = client.get("/api/deals?active_preset=false").json()
        assert history["total"] == 7
        flags = {deal["external_id"]: deal["outside_preset"] for deal in history["deals"]}
        assert flags["gpu-1"] is False
        assert flags["ram-1"] is False
        assert flags["gpu-2"] is True
        assert flags["pc-1"] is True
        assert flags["ram-2"] is True
        assert any("above the £100.00 maximum" in reason for reason in
                   next(d for d in history["deals"] if d["external_id"] == "gpu-2")["preset_reasons"])

        # A marketplace toggle applies to stored rows immediately.
        _save_config(db, [GPU, RAM, MICE],
                     marketplaces={"vinted": False, "gumtree": True, "ebay": True})
        after = client.get("/api/deals").json()
        assert {deal["external_id"] for deal in after["deals"]} == {"gpu-1"}

        # A budget change applies immediately too; no rescan is required.
        _save_config(db, [GPU, RAM, MICE], max_price=70)
        assert client.get("/api/deals").json()["total"] == 0

        # And history is still complete and untouched.
        assert client.get("/api/deals?active_preset=false").json()["total"] == 7
        assert db.scalar("SELECT COUNT(*) FROM listings") == 7


def test_overview_and_health_statistics_follow_the_preset():
    db = Database(":memory:")
    _seed_feed(db)
    with TestClient(create_app(db, autostart=False)) as client:
        overview = client.get("/api/overview").json()
        assert overview["deals"] == 2
        assert overview["hidden_by_preset"] == 5
        assert overview["active_listings"] == 2
        assert overview["active_listings_all"] == 7
        assert {row["marketplace"] for row in overview["marketplaces"]} == {"ebay", "vinted"}
        health = client.get("/api/health").json()
        assert health["counts"]["deals"] == 2
        assert health["counts"]["hidden_by_preset"] == 5


def test_auctions_have_their_own_endpoint_and_verification_states():
    db = Database(":memory:")
    _save_config(db, [GPU, RAM, MICE])
    verified = _gpu(
        db, "auc-verified", "NVIDIA GeForce RTX 3060 12GB Graphics Card", 70,
        is_auction=True, shipping=5, ends_in_minutes=8,
    )
    _deal(db, verified, expected_resale=160, net_profit=45, purchase_total=75,
          evidence="strong", max_bid=105)
    verify = _gpu(
        db, "auc-verify", "NVIDIA GeForce RTX 4060 8GB Graphics Card", 90,
        is_auction=True, ends_in_minutes=45,
    )
    _deal(db, verify, expected_resale=None, net_profit=0, purchase_total=90,
          is_deal=False, evidence="insufficient", max_bid=None)
    unclear = _gpu(
        db, "auc-unclear", "Graphics card spares or repair bundle", 30,
        is_auction=True, ends_in_minutes=9,
    )
    db.execute("UPDATE listings SET product_key=NULL, product_name=NULL WHERE id=?", (unclear,))
    _deal(db, unclear, expected_resale=None, net_profit=0, purchase_total=30,
          is_deal=False, evidence="insufficient", max_bid=None)
    over_budget = _gpu(
        db, "auc-over", "NVIDIA GeForce RTX 4070 Graphics Card", 140,
        is_auction=True, ends_in_minutes=6,
    )
    _deal(db, over_budget, expected_resale=400, net_profit=200, purchase_total=140,
          evidence="strong", max_bid=260)
    faulty = _gpu(
        db, "auc-faulty", "NVIDIA GeForce RTX 3060 Ti faulty no display", 50,
        is_auction=True, ends_in_minutes=20, faulty=True,
    )
    _deal(db, faulty, expected_resale=120, net_profit=50, purchase_total=50,
          evidence="moderate", max_bid=80)
    above = _gpu(
        db, "auc-above", "NVIDIA GeForce RTX 3060 12GB Graphics Card", 95,
        is_auction=True, ends_in_minutes=30,
    )
    _deal(db, above, expected_resale=120, net_profit=-10, purchase_total=95,
          is_deal=False, evidence="moderate", max_bid=85)
    ended = _gpu(
        db, "auc-ended", "NVIDIA GeForce RTX 3060 12GB Graphics Card", 60,
        is_auction=True, ends_in_minutes=-10,
    )
    _deal(db, ended)
    backfill_scan_leafs(db)
    # The bundle title is generic, but the stored taxonomy mapping is what the
    # workspace filters on; make this fixture's category explicit.
    db.execute("UPDATE listings SET scan_leaf=? WHERE id=?", (GPU, unclear))

    with TestClient(create_app(db, autostart=False)) as client:
        # Auctions never appear as Buy It Now deals.
        assert client.get("/api/deals").json()["total"] == 0
        # The auction workspace lists them, including candidates that are not
        # promoted, but excludes ended and preset-incompatible rows.
        response = client.get("/api/auctions").json()
        assert response["total"] == 5
        by_id = {item["external_id"]: item for item in response["auctions"]}
        assert "auc-ended" not in by_id
        assert "auc-over" not in by_id
        assert by_id["auc-verified"]["verification_status"] == "verified"
        assert by_id["auc-verified"]["profit_supported"] is True
        assert by_id["auc-verified"]["total_cost"] == 75
        assert by_id["auc-verify"]["verification_status"] == "needs_verification"
        assert by_id["auc-verify"]["profit_supported"] is False
        assert by_id["auc-unclear"]["verification_status"] == "needs_verification"
        assert by_id["auc-faulty"]["verification_status"] == "rejected"
        assert by_id["auc-above"]["verification_status"] == "rejected"
        assert response["counts"] == {
            "all": 5, "verified": 1, "needs_verification": 2, "rejected": 2,
        }
        # Ending-soon filter is a backend filter and ordering is soonest first.
        soon = client.get("/api/auctions?ending_within=10").json()
        assert soon["total"] == 2
        assert [item["external_id"] for item in soon["auctions"]] == [
            "auc-verified", "auc-unclear",
        ]
        verified_only = client.get("/api/auctions?status=verified").json()
        assert [item["external_id"] for item in verified_only["auctions"]] == ["auc-verified"]
        history = client.get("/api/auctions?active_preset=false").json()
        assert history["total"] == 6
        over = next(item for item in history["auctions"] if item["external_id"] == "auc-over")
        assert over["outside_preset"] is True
        assert over["verification_status"] == "rejected"
        assert "above the £100.00 maximum" in over["verification_reason"]
        assert history["preset"]["hidden"] == 1
        # Diagnostics are present even when a filter would show no rows.
        empty = client.get("/api/auctions?ending_within=5").json()
        assert empty["total"] == 0
        assert empty["diagnostics"]["source"]["marketplace"] == "ebay"


def test_discord_deals_cannot_bypass_a_disabled_preset(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    db = Database(":memory:")
    _save_config(db, [GPU])
    visible = _gpu(db, "notify-1", "NVIDIA GeForce RTX 3060 12GB Graphics Card", 80)
    _deal(db, visible)
    disabled = _listing(
        db, "notify-2", "Nintendo Switch OLED Console", 90,
        category="console", product_key="console:switch-oled",
        product_name="Nintendo Switch OLED", kind="console",
    )
    _deal(db, disabled, product_key="console:switch-oled", kind="console")
    over_budget = _gpu(db, "notify-3", "NVIDIA GeForce RTX 4070 Graphics Card", 400)
    _deal(db, over_budget)
    backfill_scan_leafs(db)
    db.set_settings({"notifications_enabled": "true"})

    notifier = Notifier(db)
    sent: list[str] = []

    async def fake_send(webhook, *, content, embed, listing_id=None, channel="discord",
                        record_notification=True):
        sent.append(embed["title"])
        return True

    monkeypatch.setattr(
        notifier, "_webhook", AsyncMock(return_value="https://discord.com/api/webhooks/x")
    )
    monkeypatch.setattr(notifier, "_send", fake_send)
    count = asyncio.run(notifier.notify_new_deals(limit=10))
    assert count == 1
    assert len(sent) == 1
    assert "3060" in sent[0]
    db.close()


def test_legacy_rows_are_classified_without_rewriting_history():
    db = Database(":memory:")
    _save_config(db, [GPU])
    listing_id = _listing(
        db, "legacy-1", "NVIDIA GeForce RTX 3060 12GB Graphics Card", 80
    )
    assert db.scalar("SELECT scan_leaf FROM listings WHERE id=?", (listing_id,)) is None
    assert backfill_scan_leafs(db) == 1
    assert db.scalar("SELECT scan_leaf FROM listings WHERE id=?", (listing_id,)) == GPU
    db.close()
