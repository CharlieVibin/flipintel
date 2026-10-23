"""CSV evidence, calibration and review-workflow regressions."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.calibration import build_calibration
from app.db import Database
from app.evidence import preview_import, template_csv
from app.identification import identify_listing
from app.main import create_app
from app.utils import iso
from app.valuation import ValuationConfig, ValuationEngine

FIXTURES = Path(__file__).parent / "fixtures" / "review_titles.json"


def _csv(rows: list[str], header: str | None = None) -> str:
    head = header or (
        "product_key,title,marketplace,sold_price,postage,condition,sale_date,"
        "currency,kind,storage_gb,variants,url,notes,source_id"
    )
    return head + "\n" + "\n".join(rows) + "\n"


def _add_listing(
    db: Database,
    *,
    marketplace: str = "vinted",
    external_id: str,
    title: str,
    price: float,
    key: str = "gpu:rtx-4070-12gb",
    kind: str = "gpu",
) -> int:
    now = iso()
    return db.execute(
        """
        INSERT INTO listings(
            marketplace, external_id, url, title, price, condition_norm,
            listing_kind, category, product_key, product_name,
            identification_confidence, first_seen, last_seen, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'good', ?, ?, ?, ?, 0.93, ?, ?, 'active', ?, ?)
        """,
        (
            marketplace, external_id, f"https://www.{marketplace}.co.uk/item/{external_id}",
            title, price, kind, kind, key, "NVIDIA GeForce RTX 4070", now, now, now, now,
        ),
    ).lastrowid


def _add_sale(
    db: Database,
    key: str,
    price: float,
    source: str,
    source_id: str,
    *,
    kind: str = "gpu",
    days_old: int = 30,
) -> None:
    from datetime import timedelta

    from app.utils import utcnow

    when = iso(utcnow() - timedelta(days=days_old))
    db.execute(
        """
        INSERT INTO market_comps(product_key, marketplace, source, source_id, title, price,
            kind, condition_norm, is_sold, postage, observed_at, created_at, currency)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'good', 1, 0, ?, ?, 'GBP')
        """,
        (key, "ebay" if source == "ebay" else "manual", source, source_id, f"{key} sale", price, kind, when, when),
    )


def test_template_and_csv_preview_validation(tmp_path):
    db = Database(tmp_path / "csv.db")
    try:
        headers = template_csv().splitlines()[0].split(",")
        assert {"product_key", "sold_price", "sale_date", "variants", "source_id"} <= set(headers)
        content = _csv(
            [
                "gpu:rtx-4070-12gb,RTX 4070 sold,ebay,£450.00,5,very good,2026-09-01,GBP,gpu,,,https://ebay.co.uk/itm/1,note,",
                "gpu:rtx-4070-12gb,RTX 4070 bad,ebay,not-a-price,0,good,2026-09-01,GBP,gpu,,,,,",
                "gpu:rtx-4070-12gb,RTX 4070 future,ebay,500,0,good,2099-01-01,GBP,gpu,,,,,",
                "gpu:rtx-4070-12gb,RTX 4070 usd,ebay,500,0,good,2026-09-01,USD,gpu,,,,,",
            ]
        ).encode()
        result = preview_import(db, content, "sales.csv")
        assert result["counts"] == {"imported": 1, "updated": 0, "skipped": 0, "rejected": 3}
        reasons = " ".join(item["reason"] for item in result["rejected"]).lower()
        assert "sold_price" in reasons
        assert "future" in reasons
        assert "gbp" in reasons
        assert db.scalar("SELECT COUNT(*) FROM market_comps") == 0
    finally:
        db.close()


def test_csv_optional_fields_and_row_limits(tmp_path):
    db = Database(tmp_path / "optional.db")
    try:
        content = _csv(                ["gpu:rtx-3070,RTX 3070 sold,250"],
            header="product_key,title,sold_price",
        ).encode()
        result = preview_import(db, content, "minimal.csv")
        assert result["counts"]["imported"] == 1
        assert result["rows"][0]["condition"] == "unknown"
        assert any("sale_date" in warning for warning in result["warnings"])
        huge = _csv([
            f"gpu:test-{index},RTX test sale {index},ebay,100,0,good,2026-01-01,GBP,gpu,,,,,"
            for index in range(2001)
        ]).encode()
        try:
            preview_import(db, huge, "huge.csv")
            raise AssertionError("expected row limit failure")
        except ValueError as exc:
            assert "2000" in str(exc)
    finally:
        db.close()


def test_csv_import_is_idempotent_updates_and_rejects_duplicates(tmp_path):
    db = Database(tmp_path / "idempotent.db")
    try:
        content = _csv(
            [
                "gpu:rtx-4070-12gb,RTX 4070 sold,ebay,450,5,good,2026-09-01,GBP,,,,https://ebay.co.uk/itm/1,,",
                "gpu:rtx-4070-12gb,RTX 4070 sold,ebay,450,5,good,2026-09-01,GBP,,,,https://ebay.co.uk/itm/1,,",
            ]
        ).encode()
        first = preview_import(db, content, "first.csv", commit=True)
        assert first["counts"]["imported"] == 1
        assert first["counts"]["skipped"] == 1
        assert db.scalar("SELECT COUNT(*) FROM market_comps WHERE source='manual_csv'") == 1
        again = preview_import(db, content, "again.csv", commit=True)
        assert again["counts"]["imported"] == 0
        assert again["counts"]["skipped"] == 2
        corrected = content.replace(b",450,5,", b",470,5,")
        update = preview_import(db, corrected, "corrected.csv", commit=True)
        assert update["counts"]["updated"] == 1
        row = db.query_one("SELECT price FROM market_comps WHERE source='manual_csv'")
        assert row["price"] == 470
        assert db.scalar("SELECT COUNT(*) FROM market_comps WHERE source='manual_csv'") == 1
    finally:
        db.close()


def test_csv_export_roundtrip_and_formula_safety(tmp_path):
    db = Database(tmp_path / "export.db")
    try:
        content = _csv(
            ["gpu:rtx-4070-12gb,=1+1,ebay,450,5,good,2026-09-01,GBP,,,,,,,xlsx-1"]
        ).encode()
        result = preview_import(db, content, "formula.csv", commit=True)
        assert result["counts"]["imported"] == 1
        app = create_app(db=db, autostart=False)
        with TestClient(app) as client:
            export = client.get("/api/evidence/export?source=user")
            assert export.status_code == 200
            assert "'=1+1" in export.text
            assert "evidence_type" in export.text
            assert client.get("/api/evidence/template").headers["content-type"].startswith("text/csv")
    finally:
        db.close()


def test_evidence_api_permissions_and_corrections(tmp_path):
    db = Database(tmp_path / "permissions.db")
    try:
        _add_sale(db, "gpu:rtx-4070-12gb", 450, "ebay", "independent-1")
        app = create_app(db=db, autostart=False)
        with TestClient(app) as client:
            automatic = db.query_one("SELECT id FROM market_comps WHERE source='ebay'")
            assert client.delete(f"/api/comps/{automatic['id']}").status_code == 403
            imported = client.post(
                "/api/evidence/import",
                json={
                    "filename": "one.csv",
                    "content": _csv([
                        "gpu:rtx-4070-12gb,RTX 4070 sale,ebay,455,0,good,2026-09-01,GBP,gpu,,,,,import-1"
                    ]),
                    "commit": True,
                },
            ).json()
            assert imported["counts"]["imported"] == 1
            evidence_id = client.get("/api/evidence?source=user&limit=10").json()["evidence"][0]["id"]
            update = client.patch(f"/api/evidence/{evidence_id}", json={"price": 465, "condition": "very_good"})
            assert update.status_code == 200
            assert update.json()["comp"]["price"] == 465
            assert update.json()["comp"]["condition_norm"] == "very_good"
            assert client.patch(f"/api/evidence/{evidence_id}", json={"url": "javascript:alert(1)"}).status_code == 422
            assert client.delete(f"/api/evidence/{evidence_id}").status_code == 200
            assert db.scalar("SELECT COUNT(*) FROM market_comps WHERE source='ebay'") == 1
    finally:
        db.close()


def test_calibration_is_not_invented_from_thin_or_user_only_evidence(tmp_path):
    db = Database(tmp_path / "calibration-thin.db")
    try:
        for index in range(3):
            key = f"gpu:test-{index}"
            for sale in range(2):
                _add_sale(db, key, 80, "manual_csv", f"user-{index}-{sale}", days_old=10)
            for ask in range(5):
                _add_listing(db, external_id=f"ask-{index}-{ask}", title=f"GPU {index} ask {ask}", price=100, key=key)
        report = build_calibration(db)
        assert report.status == "insufficient"
        assert report.active is False
        assert report.user_sold >= 6
        assert report.categories
        assert all(category.active is False for category in report.categories)
        assert report.category_factors == {}
    finally:
        db.close()


def test_calibration_uses_held_out_products_when_evidence_is_sufficient(tmp_path):
    db = Database(tmp_path / "calibration-good.db")
    try:
        for index in range(6):
            key = f"gpu:test-{index}"
            for sale in range(2):
                _add_sale(db, key, 80, "ebay", f"sale-{index}-{sale}", days_old=20)
            for ask in range(5):
                _add_listing(db, external_id=f"ask-{index}-{ask}", title=f"GPU {index} ask {ask}", price=100, key=key)
        report = build_calibration(db)
        assert report.active is True
        assert report.status == "active"
        assert report.matched_products == 6
        assert report.independent_sold == 12
        category = report.categories[0]
        assert category.active is True
        assert category.category == "gpu"
        assert category.holdout_products >= 2
        assert category.holdout_mae_pct is not None
        assert category.factor and 0.78 < category.factor < 0.86
        assert report.category_factors["gpu"] == category.factor
        config = ValuationConfig.from_db(db)
        config.category_factors = dict(report.category_factors)
        config.calibration_active = True
        engine = ValuationEngine(db)
        engine.refresh(limit=100)
        row = db.query_one("SELECT breakdown FROM deals LIMIT 1")
        breakdown = json.loads(row["breakdown"])
        assert breakdown["assumptions"]["calibration_active"] is True
        assert breakdown["assumptions"]["base_asking_to_sold_factor"] == 0.85
    finally:
        db.close()


def test_resale_range_and_conservative_profit_are_evidence_gated(tmp_path):
    db = Database(tmp_path / "ranges.db")
    try:
        for index in range(6):
            _add_listing(db, external_id=f"ask-{index}", title=f"RTX 4070 ask {index}", price=300 + index * 5)
        candidate = _add_listing(db, external_id="candidate", title="RTX 4070 candidate", price=220)
        for index in range(5):
            _add_sale(db, "gpu:rtx-4070-12gb", 400 + index * 10, "ebay", f"sale-{index}", days_old=20)
        engine = ValuationEngine(db)
        engine.refresh(limit=100)
        row = db.query_one("SELECT * FROM deals WHERE listing_id=?", (candidate,))
        assert row["resale_low"] is not None
        assert row["resale_high"] is not None
        assert row["net_profit_conservative"] is not None
        assert row["net_profit_conservative"] < row["net_profit"]
        breakdown = json.loads(row["breakdown"])
        assert breakdown["resale"]["range_supported"] is True
        assert breakdown["resale"]["conservative"] is not None
    finally:
        db.close()


def test_review_decisions_persist_and_do_not_boost_confidence(tmp_path):
    db = Database(tmp_path / "review-decisions.db")
    try:
        for index in range(6):
            _add_listing(db, external_id=f"ask-{index}", title=f"RTX 4070 ask {index}", price=300)
        candidate = _add_listing(db, external_id="cheap", title="RTX 4070 very cheap", price=60)
        app = create_app(db=db, autostart=False)
        with TestClient(app) as client:
            ValuationEngine(db).refresh(limit=100)
            before = client.get(f"/api/deals/{candidate}").json()
            assert before["needs_review"] is True
            response = client.post(
                f"/api/deals/{candidate}/review",
                json={"decision": "approved", "note": "Verified with seller that it is complete"},
            )
            assert response.status_code == 200
            assert response.json()["confidence_boosted"] is False
            after = client.get(f"/api/deals/{candidate}").json()
            assert after["confidence"] == before["confidence"]
            # Approval removes the risk gate and makes the listing eligible,
            # but does not raise the modelled confidence or bypass thresholds.
            assert after["is_deal"] is True
            assert after["review_decision"] == "approved"
            assert after["risk_score"] == before["risk_score"]
            assert client.get("/api/deals?review=1").json()["total"] == 0
            assert client.get("/api/deals?review=1&review_status=approved").json()["total"] == 1
            assert client.delete(f"/api/deals/{candidate}/review").status_code == 200
            assert client.get("/api/deals?review=1").json()["total"] == 1
    finally:
        db.close()


def test_review_correction_updates_identification_and_survives_refresh(tmp_path):
    db = Database(tmp_path / "review-correction.db")
    try:
        for index in range(6):
            _add_listing(db, external_id=f"ask-{index}", title=f"RTX 4070 ask {index}", price=300)
        candidate = _add_listing(
            db, external_id="misidentified", title="NVIDIA RTX 4070 bargain", price=60,
            key="console:ps5", kind="console",
        )
        app = create_app(db=db, autostart=False)
        with TestClient(app) as client:
            response = client.post(
                f"/api/deals/{candidate}/review",
                json={
                    "decision": "corrected",
                    "product_key": "gpu:rtx-4070-12gb",
                    "kind": "gpu",
                    "product_name": "NVIDIA RTX 4070 12GB",
                },
            )
            assert response.status_code == 200, response.text
            row = db.query_one("SELECT product_key, listing_kind FROM listings WHERE id=?", (candidate,))
            assert row["product_key"] == "gpu:rtx-4070-12gb"
            assert row["listing_kind"] == "gpu"
            ValuationEngine(db).refresh(limit=100)
            decision = db.query_one("SELECT decision FROM review_decisions WHERE listing_id=?", (candidate,))
            assert decision["decision"] == "corrected"
    finally:
        db.close()


def test_real_review_titles_are_stable_identification_fixtures():
    fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert len(fixtures) >= 30
    for fixture in fixtures:
        result = identify_listing(fixture["title"])
        assert result.product_key == fixture["expect_key"], fixture["title"]
        assert result.category == fixture["expect_category"], fixture["title"]
        if "expect_bundle" in fixture:
            assert result.is_bundle is fixture["expect_bundle"], fixture["title"]
        for flag in fixture.get("expect_flags", []):
            assert flag in result.flags, f"{fixture['title']} missing {flag}: {result.flags}"
        decisive_flags = {"accessory_or_box", "parts_only", "console_game", "multiple_models"}
        if fixture["expect_key"] is None and decisive_flags.intersection(fixture.get("expect_flags", [])):
            assert result.confidence == 0, fixture["title"]


def test_database_migration_keeps_legacy_tables_and_adds_columns(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE market_comps (id INTEGER PRIMARY KEY, product_key TEXT NOT NULL, "
        "marketplace TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL, "
        "title TEXT NOT NULL, price REAL NOT NULL, kind TEXT, condition_norm TEXT, "
        "is_sold INTEGER NOT NULL DEFAULT 0, url TEXT, observed_at TEXT NOT NULL, "
        "UNIQUE(source, source_id))"
    )
    conn.execute(
        "INSERT INTO market_comps(product_key, marketplace, source, source_id, title, price, "
        "is_sold, observed_at) VALUES ('gpu:old', 'manual', 'manual', 'legacy-1', 'Old sale', 100, 1, '2025-01-01')"
    )
    conn.commit()
    conn.close()
    db = Database(path)
    try:
        columns = {row["name"] for row in db.query("PRAGMA table_info(market_comps)")}
        assert {"currency", "product_name", "condition_raw", "variants", "import_id", "updated_at"} <= columns
        assert db.scalar("SELECT COUNT(*) FROM market_comps WHERE source_id='legacy-1'") == 1
        tables = {row["name"] for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"evidence_imports", "review_decisions"} <= tables
    finally:
        db.close()
