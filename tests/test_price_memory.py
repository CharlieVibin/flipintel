"""Price-memory expiry and product-vs-listing snapshot isolation."""
from __future__ import annotations

import json
from datetime import timedelta

from app.db import Database
from app.scanner import Scanner
from app.utils import iso, utcnow
from app.valuation import ValuationEngine


def _listing(
    db: Database,
    *,
    external_id: str,
    title: str,
    price: float,
    key: str = "gpu:rtx-4070-12gb",
    kind: str = "gpu",
    marketplace: str = "vinted",
    condition: str = "good",
    description: str = "",
) -> int:
    now = iso()
    return db.execute(
        """
        INSERT INTO listings(
            marketplace, external_id, url, title, price, condition_norm,
            listing_kind, category, product_key, product_name,
            identification_confidence, description, first_seen, last_seen, status,
            raw_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.93, ?, ?, ?, 'active', ?, ?, ?)
        """,
        (
            marketplace, external_id, f"https://www.{marketplace}.co.uk/item/{external_id}",
            title, price, condition, kind, kind, key, "NVIDIA GeForce RTX 4070",
            description, now, now, json.dumps({}), now, now,
        ),
    ).lastrowid


def test_stale_snapshot_is_refreshed_without_losing_history(tmp_path):
    db = Database(tmp_path / "stale.db")
    try:
        for index in range(5):
            _listing(db, external_id=f"comp-{index}", title=f"RTX 4070 12GB {index}", price=420)
        candidate = _listing(
            db, external_id="candidate", title="RTX 4070 12GB", price=260,
        )
        engine = ValuationEngine(db)
        engine.refresh(full=True)
        first = db.query_one(
            "SELECT id, refreshed_at FROM valuation_snapshots WHERE product_key=? "
            "ORDER BY id DESC LIMIT 1",
            ("gpu:rtx-4070-12gb",),
        )
        assert first is not None
        old_time = iso(utcnow() - timedelta(days=9))
        db.execute(
            "UPDATE valuation_snapshots SET refreshed_at=? WHERE id=?",
            (old_time, first["id"]),
        )
        before = db.scalar(
            "SELECT COUNT(*) FROM valuation_snapshots WHERE product_key=?",
            ("gpu:rtx-4070-12gb",),
        )
        # No listing changed; only the seven-day reuse window expired.
        engine.refresh(full=False)
        rows = db.query(
            "SELECT id, refreshed_at FROM valuation_snapshots WHERE product_key=? "
            "ORDER BY id ASC",
            ("gpu:rtx-4070-12gb",),
        )
        assert len(rows) > before
        assert rows[0]["id"] == first["id"]
        assert rows[0]["refreshed_at"] == old_time
        assert rows[-1]["id"] > first["id"]
        deal = db.query_one(
            "SELECT valuation_refreshed_at FROM deals WHERE listing_id=?", (candidate,)
        )
        assert deal is not None and deal["valuation_refreshed_at"]
    finally:
        db.close()


def test_listing_contents_discount_does_not_enter_product_memory(tmp_path):
    db = Database(tmp_path / "contents.db")
    try:
        for index in range(6):
            _listing(
                db, external_id=f"ps5-{index}", title=f"PlayStation 5 console {index}",
                price=320, key="console:ps5", kind="console",
            )
        candidate = _listing(
            db, external_id="console-only", title="Sony PlayStation 5 Console",
            price=200, key="console:ps5", kind="console",
            description="Console only, no controller or cables included",
        )
        Scanner(db, collectors={})._reidentify_existing()
        ValuationEngine(db).refresh(full=True)
        deal = db.query_one(
            "SELECT expected_resale, breakdown FROM deals WHERE listing_id=?", (candidate,)
        )
        snapshot = db.query_one(
            "SELECT resale_good FROM valuation_snapshots WHERE product_key='console:ps5' "
            "AND invalidated_at IS NULL ORDER BY id DESC LIMIT 1"
        )
        assert deal is not None and snapshot is not None
        breakdown = json.loads(deal["breakdown"])
        assert any("12%" in note for note in breakdown["valuation_notes"])
        # The deal carries the missing-contents discount; product memory must not.
        assert deal["expected_resale"] < snapshot["resale_good"]
    finally:
        db.close()
