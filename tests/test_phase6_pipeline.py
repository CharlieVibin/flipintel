"""Phase 6 regressions: what is sold, eligibility and incremental maintenance."""
from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.db import Database
from app.eligibility import EXCLUDED, NEEDS_VERIFICATION, RECOMMENDED, assess
from app.main import create_app
from app.identification import (
    IDENTIFICATION_VERSION,
    identify_listing,
)
from app.scanner import Scanner, identity_hash
from app.utils import hours_ago, iso
from app.valuation import ValuationEngine


def _listing(
    db: Database,
    *,
    external_id: str,
    title: str,
    price: float,
    key: str | None = "gpu:rtx-4070-12gb",
    kind: str = "gpu",
    category: str = "gpu",
    flags: list[str] | None = None,
    version: int = 0,
    digest: str | None = None,
) -> int:
    now = iso()
    return db.execute(
        """
        INSERT INTO listings(
            marketplace, external_id, url, title, price, condition_norm,
            listing_kind, category, product_key, product_name,
            identification_confidence, flags, first_seen, last_seen, status,
            identity_version, identity_hash, sale_role, created_at, updated_at
        ) VALUES ('ebay', ?, ?, ?, ?, 'good', ?, ?, ?, ?, 0.9, ?, ?, ?, 'active',
                  ?, ?, 'component', ?, ?)
        """,
        (
            external_id, f"https://www.ebay.co.uk/itm/{external_id}", title, price,
            kind, category, key, "Test product", json.dumps(flags or []),
            now, now, version, digest, now, now,
        ),
    ).lastrowid


def test_physical_product_wins_over_compatibility_and_head_noun():
    bracket = identify_listing("RTX 5000 low-profile bracket")
    assert bracket.product_key is None
    assert bracket.sale_role == "accessory"
    assert bracket.confidence == 0.0

    keycap = identify_listing("RTX 5080/5090 artisan keycap")
    assert keycap.product_key is None
    assert keycap.sale_role == "accessory"

    card = identify_listing("Nintendo Switch 1TB microSD card")
    assert card.product_key is None
    assert card.sale_role == "media"

    drive = identify_listing("SK Hynix 256GB M.2 2230 NVMe PCIe SSD Steam Deck Dell")
    assert drive.kind == "ssd"
    assert drive.product_key and drive.product_key.startswith("ssd:")

    box = identify_listing("Empty PS5 box")
    assert box.product_key is None
    assert box.sale_role == "packaging"

    case = identify_listing("Otter box iPhone 17 PRO")
    assert case.product_key is None
    assert case.sale_role == "accessory"


def test_complete_system_is_not_its_installed_drive():
    system = identify_listing(
        'Dell Pro Max 16 Plus,Ultra 7 265HX,32GB MEM,1TB NVME,RTX Pro 2000 8GB,FHD+,16"'
    )
    assert system.kind == "laptop"
    assert system.product_key is None  # no exact model. Better blank than a fake SSD.

    toshiba = identify_listing(
        "Dynabook Toshiba Satellite Pro Intel Core i5 10th Gen | 8GB RAM | 256GB SSD"
    )
    assert toshiba.kind == "laptop"

    nas = identify_listing("QNAP TBS-H574TX 5-Bay NAS 5x SAMSUNG 990 Evo PLUS 2TB M.2")
    assert nas.kind == "pc"
    assert nas.product_key is None


def test_complete_apple_desktops_are_not_storage_components():
    for title in (
        "Apple Mac mini (M2, 2023) - 8GB RAM, 512GB SSD | Mint Condition with Original Box & Accessories",
        "Apple Mac Studio M2 Max 32GB 512GB SSD",
        "Apple iMac 24 M1 8GB 256GB SSD",
        "Apple iMac 2017 i7 1TB Samsung ssd 64GB ram",
    ):
        result = identify_listing(title)
        assert result.kind == "pc", (title, result.kind, result.product_key)
        assert result.sale_role == "device", (title, result.sale_role)
        assert not (result.product_key or "").startswith("ssd:"), title


def test_windows_complete_system_is_not_its_installed_drive():
    result = identify_listing(
        "Dell 3630 NVIDIA P2000 (Can run local LLM Ai) GPU, 16GB RAM 256GB NVMe Win11Pro"
    )
    assert result.kind == "pc"
    assert not (result.product_key or "").startswith("ssd:")


def test_console_with_expansion_ssd_does_not_become_an_ssd():
    result = identify_listing(
        "Xbox Series X 1TB Console, Elite Wireless Controller Series 2, "
        "WD Black C50 1TB Expansion SSD"
    )
    assert result.kind == "console"
    assert result.product_key == "console:xbox-series-x"
    assert result.is_bundle is True


def test_storage_peer_median_rejects_outlier_asking_prices(tmp_path):
    db = Database(tmp_path / "storage-peer.db")
    try:
        for index in range(7):
            _listing(
                db,
                external_id=f"peer-{index}",
                title=f"OEM {index} 1TB NVMe SSD",
                price=40 + index * 5,
                key=f"ssd:nvme:m2:1tb:peer{index}",
                kind="ssd",
                category="ssd",
            )
        target_key = "ssd:nvme:m2:1tb:samsung:990-pro"
        expensive = _listing(
            db,
            external_id="expensive-drive",
            title="Samsung 990 Pro 1TB NVMe SSD",
            price=569.0,
            key=target_key,
            kind="ssd",
            category="ssd",
        )
        candidate = _listing(
            db,
            external_id="candidate-drive",
            title="Samsung 990 Pro 1TB NVMe SSD",
            price=50.0,
            key=target_key,
            kind="ssd",
            category="ssd",
        )
        engine = ValuationEngine(db)
        accepted = engine._market_comps(
            target_key,
            "ssd",
            candidate,
            {"product_key": target_key, "title": "Samsung 990 Pro 1TB NVMe SSD"},
        )
        assert all(float(row["price"]) <= 110 for row in accepted), accepted
        assert any(
            "twice the" in str(row.get("reason") or "")
            for row in engine._last_rejected
        ), engine._last_rejected
        ceiling = engine._storage_peer_ceiling("ssd", target_key)
        assert ceiling is not None and ceiling[0] == 110.0 and ceiling[1] == 7
        assert expensive
    finally:
        db.close()


def test_sparse_console_pool_rejects_a_far_below_family_ask(tmp_path):
    db = Database(tmp_path / "family-floor.db")
    try:
        key = "console:steam-deck-lcd:1tb:upgraded"
        for index, capacity in enumerate(("64gb", "256gb", "512gb")):
            for copy in range(2):
                _listing(
                    db,
                    external_id=f"deck-{index}-{copy}",
                    title=f"Steam Deck LCD {capacity}",
                    price=250 + index * 35 + copy * 5,
                    key=f"console:steam-deck-lcd:{capacity}",
                    kind="console",
                    category="console",
                )
        _listing(
            db,
            external_id="too-cheap-deck",
            title="Steam Deck LCD with 1tb",
            price=79.88,
            key=key,
            kind="console",
            category="console",
        )
        candidate = _listing(
            db,
            external_id="target-deck",
            title="Steam deck 1tb lcd",
            price=180.0,
            key=key,
            kind="console",
            category="console",
        )
        engine = ValuationEngine(db)
        accepted = engine._market_comps(
            key,
            "console",
            candidate,
            {"product_key": key, "title": "Steam deck 1tb lcd"},
        )
        assert accepted == []
        assert any(
            "same product family" in str(row.get("reason") or "")
            for row in engine._last_rejected
        ), engine._last_rejected
    finally:
        db.close()


def test_revaluation_retires_a_snapshot_when_no_estimate_survives(tmp_path):
    db = Database(tmp_path / "retire-snapshot.db")
    try:
        key = "ssd:nvme:m2:1tb:orphan"
        _listing(
            db,
            external_id="orphan-drive",
            title="Orphan 1TB NVMe SSD",
            price=30.0,
            key=key,
            kind="ssd",
            category="ssd",
        )
        db.execute(
            """
            INSERT INTO valuation_snapshots(
                product_key, kind, resale_good, resale_low, resale_high,
                confidence, evidence_level, sample_size, sold_count, sources,
                method, refreshed_at
            ) VALUES (?, 'ssd', 569.0, 569.0, 569.0, 0.28, 'insufficient',
                      1, 0, '{}', 'live_asking_comps', ?)
            """,
            (key, iso()),
        )
        ValuationEngine(db).refresh(limit=100)
        snapshot = db.query_one(
            "SELECT invalidated_at, invalid_reason FROM valuation_snapshots "
            "WHERE product_key=? ORDER BY id DESC LIMIT 1",
            (key,),
        )
        assert snapshot["invalidated_at"] is not None
        assert "no supported estimate" in snapshot["invalid_reason"]
    finally:
        db.close()


def test_motherboard_model_is_not_merged_with_chipset_pool():
    premium = identify_listing("Gigabyte Z790 AORUS Master X")
    budget = identify_listing("ASUS Prime H610M-K Intel H610 LGA 1700 Micro ATX")
    assert premium.product_key == "mobo:gigabyte:z790-aorus-master-x"
    assert budget.product_key and budget.product_key.startswith("mobo:asus:h610")
    assert premium.product_key != budget.product_key


def test_capacity_decimal_is_read_once():
    drive = identify_listing("3.84TB Micron 5400 Pro 2.5 SATA SSD")
    assert drive.attrs["capacity_gb"] == 3932
    assert drive.product_key and drive.product_key.endswith("3.84tb:micron")


def _row(**overrides):
    row = {
        "product_key": "gpu:rtx-4070-12gb",
        "flags_list": [],
        "sale_role": "component",
        "is_faulty": 0,
        "is_bundle": 0,
        "is_wanted": 0,
    }
    row.update(overrides)
    return row


def test_eligibility_separates_junk_review_and_recommendations():
    role, reason = assess(
        _row(sale_role="accessory", flags_list=["accessory_or_box"]),
        is_deal=True, needs_review=False, expected_resale=500,
        purchase_total=20, net_profit=200,
    )
    assert role == EXCLUDED and reason

    role, _ = assess(
        _row(flags_list=["untested"]),
        is_deal=False, needs_review=True, expected_resale=100,
        purchase_total=50, net_profit=20, risk_level="medium",
    )
    assert role == NEEDS_VERIFICATION

    role, reason = assess(
        _row(), is_deal=False, needs_review=False, expected_resale=1000,
        purchase_total=15, net_profit=700,
    )
    assert role == EXCLUDED and "under 5%" in (reason or "")

    role, _ = assess(
        _row(), is_deal=True, needs_review=False, expected_resale=300,
        purchase_total=150, net_profit=60,
    )
    assert role == RECOMMENDED

    role, _ = assess(
        _row(product_key=None), is_deal=False, needs_review=True,
        expected_resale=None, purchase_total=10, net_profit=None,
    )
    assert role == EXCLUDED


def test_incremental_identification_only_processes_changed_rows(tmp_path):
    db = Database(tmp_path / "incremental.db")
    try:
        first = _listing(
            db, external_id="bracket", title="RTX 5000 low-profile bracket",
            price=20, key="gpu:rtx-5000", kind="gpu",
        )
        # Already current and fingerprinted: it must not be reprocessed even
        # though its stored key is deliberately wrong.
        title = "RTX 5080/5090 artisan keycap"
        untouched = _listing(
            db, external_id="keycap", title=title, price=20,
            key="gpu:rtx-5080", kind="gpu",
            version=IDENTIFICATION_VERSION,
            digest=identity_hash(title, "", ""),
        )
        scanner = Scanner(db)
        assert scanner._reidentify_existing() == 1
        row = db.query_one("SELECT product_key, sale_role FROM listings WHERE id=?", (first,))
        assert row["product_key"] is None
        assert row["sale_role"] == "accessory"
        still = db.query_one("SELECT product_key FROM listings WHERE id=?", (untouched,))
        assert still["product_key"] == "gpu:rtx-5080"
        # A warm pass does no classifier work.
        assert scanner._reidentify_existing() == 0
        revisions = db.scalar("SELECT COUNT(*) FROM identity_revisions") or 0
        assert revisions == 1
    finally:
        db.close()


def test_startup_returns_before_maintenance_finishes(tmp_path):
    db = Database(tmp_path / "startup.db")
    try:
        async def run():
            scanner = Scanner(db)
            scanner.scheduled_enabled = False
            await scanner.start()
            assert scanner._maintenance_task is not None
            await scanner._maintenance_task
            assert scanner.maintenance_ready is True
            assert scanner.maintenance()["phase"] == "idle"
            await scanner.stop()

        asyncio.run(run())
    finally:
        db.close()


def test_excluded_junk_never_reaches_feed_or_review(tmp_path):
    db = Database(tmp_path / "excluded.db")
    try:
        for index in range(6):
            _listing(
                db, external_id=f"ask-{index}", title=f"RTX 4070 12GB ask {index}", price=300
            )
        junk = _listing(
            db, external_id="otter", title="Otter box iPhone 17 PRO", price=20,
            key=None, kind="other", category="other", flags=["accessory_or_box"],
        )
        app = create_app(db=db, autostart=False)
        with TestClient(app) as client:
            ValuationEngine(db).refresh(limit=100)
            row = db.query_one(
                "SELECT eligibility, needs_review, is_deal FROM deals WHERE listing_id=?",
                (junk,),
            )
            assert row["eligibility"] == "excluded"
            assert row["needs_review"] == 0
            assert row["is_deal"] == 0
            feed = client.get("/api/deals").json()
            assert all(item["id"] != junk for item in feed["deals"])
            review = client.get("/api/deals?review=1").json()
            assert review["total"] == 0
    finally:
        db.close()


def test_full_refresh_resumes_from_cursor(tmp_path):
    db = Database(tmp_path / "cursor.db")
    try:
        ids = [
            _listing(db, external_id=f"ask-{index}", title=f"RTX 4070 12GB ask {index}", price=300)
            for index in range(6)
        ]
        engine = ValuationEngine(db)
        assert engine.refresh(limit=100)["full"] == 1
        signature = db.scalar("SELECT value FROM valuation_state WHERE key='config_signature'")
        # Pretend a prior full pass stopped after the third row.
        db.execute("UPDATE valuation_state SET value=? WHERE key='last_full_at'", (hours_ago(7),))
        db.execute(
            "INSERT INTO valuation_state(key, value, updated_at) VALUES ('full_cursor', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (str(ids[2]), iso()),
        )
        db.execute(
            "INSERT INTO valuation_state(key, value, updated_at) VALUES ('cursor_signature', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (str(signature), iso()),
        )
        result = engine.refresh(limit=100)
        assert result["full"] == 1
        assert result["evaluated"] == 3
        cursor = db.scalar("SELECT value FROM valuation_state WHERE key='full_cursor'")
        assert cursor == "0"
    finally:
        db.close()
