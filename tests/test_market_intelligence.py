"""Market-intelligence regressions: credentials, risk, coverage and valuation evidence."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.collectors.ebay import EbayCollector
from app.credentials import CredentialStore, credential_store
from app.db import Database
from app.identification import identify_listing
from app.main import create_app
from app.risk import assess_listing
from app.scanner import Scanner
from app.usage import record_api_usage, usage_snapshot
from app.utils import hours_ago, iso
from app.valuation import ValuationConfig, ValuationEngine, _estimate_from_rows


def _listing(db: Database, *, external_id: str, title: str, price: float,
             key: str = "gpu:rtx-4070-12gb", kind: str = "gpu",
             category: str = "gpu", marketplace: str = "vinted",
             condition: str = "good", raw: dict | None = None) -> int:
    now = iso()
    return db.execute(
        """
        INSERT INTO listings(
            marketplace, external_id, url, title, price, condition_norm,
            listing_kind, category, product_key, product_name,
            identification_confidence, first_seen, last_seen, status,
            raw_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.93, ?, ?, 'active', ?, ?, ?)
        """,
        (
            marketplace, external_id, f"https://www.{marketplace}.co.uk/item/{external_id}",
            title, price, condition, kind, category, key, "NVIDIA GeForce RTX 4070",
            now, now, json.dumps(raw or {}), now, now,
        ),
    ).lastrowid


def test_credential_store_persists_without_exposing_secret(tmp_path, monkeypatch):
    for key in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_ENV"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    path.write_text("DISCORD_WEBHOOK_URL=https://example.invalid/hook\n", encoding="utf-8")
    store = CredentialStore(path)

    assert store.ebay_configured is False
    status = store.save_ebay("Test-App-PRD-12345678-abcd", "secret-value-12345678", "production")
    assert status["configured"] is True
    assert status["credential_source"] == "local .env"
    assert "secret-value-12345678" not in json.dumps(status)
    assert str(status["client_id_hint"]).endswith("abcd")
    text = path.read_text(encoding="utf-8")
    assert "DISCORD_WEBHOOK_URL=https://example.invalid/hook" in text
    assert "EBAY_CLIENT_ID=Test-App-PRD-12345678-abcd" in text
    assert "EBAY_ENV=production" in text

    reloaded = CredentialStore(path)
    assert reloaded.ebay_configured is True
    assert reloaded.ebay.environment == "production"
    assert reloaded.status()["configured"] is True

    cleared = store.clear_ebay()
    assert cleared["configured"] is False
    assert "EBAY_CLIENT_SECRET" not in path.read_text(encoding="utf-8")
    assert "DISCORD_WEBHOOK_URL=https://example.invalid/hook" in path.read_text(encoding="utf-8")


def test_ebay_credentials_activate_collector_without_restart(tmp_path, monkeypatch):
    for key in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_ENV"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    store = CredentialStore(path)
    monkeypatch.setattr(credential_store, "env_file", path)
    db = Database(tmp_path / "creds.db")
    collector = EbayCollector(db)
    try:
        assert collector.available[0] is False
        store.save_ebay("Test-App-PRD-87654321-abcd", "secret-value-87654321", "production")
        assert collector.available[0] is True
        assert collector.credentials.client_id.startswith("Test-App")
        store.clear_ebay()
        assert collector.available[0] is False
    finally:
        store.clear_ebay()
        db.close()


def test_ebay_parser_uk_country_cheapest_shipping_and_thumbnail(monkeypatch):
    db = Database(":memory:")
    collector = EbayCollector(db)
    try:
        item = collector._parse_item(
            {
                "title": "NVIDIA RTX 4070",
                "itemId": "v1|1|0",
                "itemWebUrl": "https://www.ebay.co.uk/itm/1",
                "price": {"value": "450", "currency": "GBP"},
                "shippingOptions": [
                    {"shippingCost": {"value": "8.00", "currency": "GBP"}},
                    {"shippingCost": {"value": "3.00", "currency": "GBP"}},
                ],
                "condition": "Used",
                "buyingOptions": ["FIXED_PRICE"],
                "itemLocation": {"country": "GB"},
                "seller": {"username": "seller", "feedbackPercentage": "98.2"},
                "thumbnailImages": [{"imageUrl": "https://i.ebayimg.com/thumb.jpg"}],
            },
            "rtx 4070",
        )
        assert item is not None
        assert item.shipping == 3.0
        assert item.image_url == "https://i.ebayimg.com/thumb.jpg"
        assert item.raw["seller_feedback"] == "98.2"
        assert collector._parse_item(
            {
                "title": "NVIDIA RTX 4070", "itemId": "v1|2|0",
                "itemWebUrl": "https://www.ebay.co.uk/itm/2",
                "price": {"value": "450", "currency": "GBP"},
                "buyingOptions": ["FIXED_PRICE"], "itemLocation": {"country": "US"},
            },
            "x",
        ) is None
    finally:
        import asyncio

        asyncio.run(collector.close())
        db.close()


def test_risk_score_does_not_call_a_low_price_fraud_alone():
    estimate = SimpleNamespace(comps=[{"adjusted_price": 100.0} for _ in range(8)])
    low_price_only = {
        "price": 10.0,
        "condition_norm": "good",
        "flags_list": [],
        "raw_json": "{}",
        "image_url": "https://example.invalid/x.jpg",
    }
    result = assess_listing(low_price_only, estimate, expected_resale=1000, purchase_total=10)
    assert result.level != "high"
    assert result.review_recommended is True
    assert result.score <= 45

    corroborated = {
        **low_price_only,
        "flags_list": ["condition_contradiction"],
    }
    strongly_suspicious = assess_listing(corroborated, estimate, expected_resale=1000, purchase_total=10)
    assert strongly_suspicious.level == "high"
    assert any("condition" in reason.lower() for reason in strongly_suspicious.reasons)


def test_condition_contradiction_and_networking_identification():
    contradiction = identify_listing(
        "Sealed brand new PS5 console",
        "Opened and used, has scratches; read description",
    )
    assert "condition_contradiction" in contradiction.flags

    router = identify_listing("TP-Link Deco XE75 mesh wifi system 3 pack")
    assert router.category == "networking"
    assert router.product_key == "network:tp-link:deco-xe75"

    unifi = identify_listing("Ubiquiti UniFi UDM Pro router")
    assert unifi.category == "networking"
    assert unifi.product_key == "network:ubiquiti:udm-pro"

    generic = identify_listing("wifi router")
    assert generic.category == "networking"
    assert generic.product_key is None


def test_aio_laptop_and_accessory_edges_do_not_create_fake_comparables():
    aio = identify_listing(
        "HP 800 G2 AIO Intel Core i3-6100 CPU 8GB RAM 128GB SSD Win 11 PC"
    )
    assert aio.category == "pc"
    assert aio.product_key.startswith("pc:")

    laptop = identify_listing("HP EliteBook 860 G9 i5-1235U 16GB 512GB NVMe SSD")
    assert laptop.category == "laptop"

    parts = identify_listing("iPhone 13 unlocked motherboard")
    assert parts.product_key is None
    assert "parts_only" in parts.flags

    drive = identify_listing("Sony PS5 Blu-ray drive for digital editions")
    assert drive.product_key is None
    assert "accessory_or_box" in drive.flags

    screen = identify_listing("Nintendo Switch OLED screen only")
    assert screen.product_key is None

    tablet = identify_listing("iPad (6th Generation) 128GB Silver")
    assert tablet.product_key == "tablet:ipad-gen6-128gb"

    generic_tablet = identify_listing("iPad 128GB")
    assert generic_tablet.product_key is None

    monitor = identify_listing("LG 27UL500 27 inch 4K UHD Gaming Monitor")
    assert monitor.product_key == "monitor:27in-4k:lg:ul500"


def test_phone_and_laptop_variants_are_not_mixed_silently():
    storage = identify_listing("iPhone 13 128")
    assert storage.product_key == "phone:iphone-13-128gb"
    assert "variant_unclear" not in storage.flags

    unknown = identify_listing("iPhone 13")
    assert unknown.product_key == "phone:iphone-13"
    assert "variant_unclear" in unknown.flags

    m2 = identify_listing("Apple MacBook Air 2022, M2 chip, 245GB")
    assert m2.product_key == "laptop:macbook-air-m2"
    assert "variant_unclear" in m2.flags

    base = identify_listing("MacBook Air M2 8GB 256GB")
    assert base.product_key == "laptop:macbook-air-m2"
    assert "variant_unclear" not in base.flags

    intel_or_m1 = identify_listing("Apple MacBook Pro 13-inch (2020)")
    assert intel_or_m1.product_key == "laptop:macbook-pro-2020-13in"
    assert "variant_unclear" in intel_or_m1.flags


def test_console_only_listing_is_flagged_and_discounted(tmp_path):
    db = Database(tmp_path / "console-only.db")
    try:
        for index in range(6):
            _listing(
                db, external_id=f"ps5-{index}", title=f"PlayStation 5 console {index}",
                price=300, key="console:ps5", kind="console", category="console",
            )
        candidate = _listing(
            db, external_id="console-only", title="Sony PlayStation 5 Console",
            price=180, key="console:ps5", kind="console", category="console",
        )
        db.execute(
            "UPDATE listings SET description=? WHERE id=?",
            ("PlayStation 5 console only, no controller or cables included", candidate),
        )
        scanner = Scanner(db, collectors={})
        scanner._reidentify_existing()
        row = db.query_one("SELECT flags FROM listings WHERE id=?", (candidate,))
        assert "contents_incomplete" in json.loads(row["flags"])
        ValuationEngine(db).refresh(limit=100)
        detail = db.query_one("SELECT breakdown FROM deals WHERE listing_id=?", (candidate,))
        breakdown = json.loads(detail["breakdown"])
        assert any("12%" in note for note in breakdown["valuation_notes"])
        assert any("contents_incomplete" in (risk.get("signals") or []) for risk in [breakdown["risk"]])
    finally:
        db.close()


def test_deep_discount_goes_to_review_without_being_called_fraud():
    comps = [
        {"title": f"iPhone 13 128GB comparable {index}", "price": 220,
         "adjusted_price": 220, "condition": "good", "is_sold": 0}
        for index in range(7)
    ]
    risk = assess_listing(
        {"price": 90, "condition_norm": "good", "flags_list": []},
        SimpleNamespace(comps=comps),
        expected_resale=220,
        purchase_total=90,
    )
    assert risk.review_recommended is True
    assert risk.level != "high"
    assert "price_anomaly" in risk.signals
    assert risk.market_ratio is not None and risk.market_ratio < 0.5


def test_cheapest_percentile_zero_still_triggers_deep_discount_review():
    comps = [
        {"title": f"RTX 4070 comparable {index}", "price": 200,
         "adjusted_price": 200, "condition": "good", "is_sold": 0}
        for index in range(7)
    ]
    risk = assess_listing(
        {"price": 110, "condition_norm": "good", "flags_list": [], "image_url": "x"},
        SimpleNamespace(comps=comps),
        expected_resale=200,
        purchase_total=110,
    )
    # 110 is 55% of the market median: not the cheapest (percentile 0), but far
    # enough below the market to require a human check. The old `or 1.0`
    # expression turned percentile zero into 1.0 and silently skipped review.
    assert risk.price_percentile == 0.0
    assert risk.market_ratio is not None and 0.5 < risk.market_ratio < 0.65
    assert risk.review_recommended is True
    assert risk.level != "high"


def test_component_bundle_is_not_mistaken_for_a_complete_pc():
    bundle = identify_listing("RTX 4070 12GB graphics card + 16GB DDR5 RAM")
    assert bundle.category == "gpu"
    assert bundle.kind == "gpu"

    complete = identify_listing("Gaming PC RTX 4070 16GB DDR5 RAM")
    assert complete.category == "pc"


def test_market_observations_are_recorded_and_risk_columns_populated(tmp_path):
    db = Database(tmp_path / "observations.db")
    try:
        for index in range(6):
            _listing(db, external_id=f"ask-{index}", title=f"RTX 4070 12GB ask {index}", price=300)
        candidate_id = _listing(
            db, external_id="cheap", title="RTX 4070 12GB bargain", price=60,
            marketplace="gumtree",
        )
        engine = ValuationEngine(db)
        result = engine.refresh(limit=100)
        assert result["observed"] == 7
        assert db.scalar("SELECT COUNT(*) FROM market_observations") == 7
        row = db.query_one("SELECT risk_score, risk_level, risk_reasons FROM deals WHERE listing_id=?", (candidate_id,))
        assert row is not None
        assert row["risk_score"] > 0
        assert row["risk_level"] in {"medium", "high"}
        assert json.loads(row["risk_reasons"])
        # Re-running refresh is idempotent for the daily observation snapshot.
        engine.refresh(limit=100)
        assert db.scalar("SELECT COUNT(*) FROM market_observations") == 7
    finally:
        db.close()


def test_approved_review_listing_can_become_a_deal_without_confidence_boost(tmp_path):
    db = Database(tmp_path / "review-approval.db")
    try:
        for index in range(7):
            _listing(db, external_id=f"normal-{index}", title=f"RTX 4070 12GB ask {index}", price=300)
        candidate = _listing(
            db, external_id="cheap-review", title="RTX 4070 12GB bargain", price=80,
            marketplace="gumtree",
        )
        engine = ValuationEngine(db)
        engine.refresh(limit=100)
        row = db.query_one("SELECT needs_review, is_deal, confidence FROM deals WHERE listing_id=?", (candidate,))
        assert row["needs_review"] == 1
        assert row["is_deal"] == 0
        confidence_before = row["confidence"]

        db.execute(
            "INSERT INTO review_decisions(listing_id, decision, decided_at) VALUES (?, 'approved', ?)",
            (candidate, iso()),
        )
        engine.refresh(limit=100)
        row = db.query_one("SELECT needs_review, is_deal, confidence FROM deals WHERE listing_id=?", (candidate,))
        assert row["needs_review"] == 0
        assert row["is_deal"] == 1
        assert row["confidence"] <= confidence_before + 0.001

        db.execute(
            "UPDATE review_decisions SET decision='rejected', decided_at=? WHERE listing_id=?",
            (iso(), candidate),
        )
        engine.refresh(limit=100)
        rejected = db.query_one("SELECT is_deal, needs_review FROM deals WHERE listing_id=?", (candidate,))
        assert rejected["is_deal"] == 0
        assert rejected["needs_review"] == 1
    finally:
        db.close()


def test_asking_pool_excludes_desperate_low_outlier():
    rows = [
        {"title": "iPad 9 64GB low outlier", "price": 50, "condition_norm": "good",
         "marketplace": "vinted", "source": "vinted", "is_sold": 0,
         "observed_at": hours_ago(24), "source_id": "low"},
    ]
    for index, price in enumerate([150, 160, 170, 180, 190, 200]):
        rows.append({
            "title": f"iPad 9 64GB ask {index}", "price": price, "condition_norm": "good",
            "marketplace": "vinted", "source": "vinted", "is_sold": 0,
            "observed_at": hours_ago(24), "source_id": f"ask-{index}",
        })
    estimate = _estimate_from_rows(rows, ValuationConfig(), 0.8, "good", [], "tablet")
    assert estimate.sample_size == 6
    assert min(float(comp["price"]) for comp in estimate.comps) >= 150
    assert estimate.resale_good is not None and estimate.resale_good > 120


def test_asking_trend_and_confidence_interval_are_explicit():
    rows = []
    for index, price in enumerate([100, 105, 95, 110]):
        rows.append({
            "title": f"recent {index}", "price": price, "condition_norm": "good",
            "marketplace": "vinted", "source": "vinted", "is_sold": 0,
            "observed_at": hours_ago(24 * 3), "source_id": f"recent-{index}",
        })
    for index, price in enumerate([200, 205, 195, 210]):
        rows.append({
            "title": f"older {index}", "price": price, "condition_norm": "good",
            "marketplace": "vinted", "source": "vinted", "is_sold": 0,
            "observed_at": hours_ago(24 * 40), "source_id": f"older-{index}",
        })
    estimate = _estimate_from_rows(rows, ValuationConfig(), 0.9, "good", [], "gpu")
    assert estimate.trend_pct is not None
    assert estimate.trend_pct < -0.4
    assert estimate.trend_note
    assert estimate.ci_low and estimate.ci_high
    assert estimate.ci_low < estimate.ci_high


def test_usage_and_category_coverage_are_recorded(tmp_path):
    db = Database(tmp_path / "usage.db")
    try:
        record_api_usage(db, "ebay", status=200, latency_ms=30)
        record_api_usage(db, "ebay", status=429, error="rate limited")
        usage = usage_snapshot(db, "ebay")
        assert usage["today"]["requests"] == 2
        assert usage["today"]["errors"] == 1
        assert usage["today"]["last_status"] == 429
        assert usage["last_request_at"]
        assert usage["last_success_at"], "a successful OAuth/Browse call must stay visible after a later error"
        assert usage["quota"]["state"] == "unknown"

        _listing(db, external_id="gpu-1", title="RTX 4070", price=300, category="gpu")
        _listing(db, external_id="ssd-1", title="Samsung 990 Pro 2TB NVMe SSD", price=100,
                 key="ssd:nvme:m2:2tb", kind="ssd", category="ssd")
        scanner = Scanner(db, collectors={})
        coverage = scanner.coverage_snapshot()
        by_key = {item["key"]: item for item in coverage["categories"]}
        assert by_key["gpu"]["active"] == 1
        assert by_key["ssd"]["active"] == 1
        assert by_key["monitor"]["coverage"] == "empty"
        assert coverage["covered_categories"] >= 0
    finally:
        db.close()


def test_ebay_credentials_api_never_returns_the_secret(tmp_path, monkeypatch):
    for key in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_ENV"):
        monkeypatch.delenv(key, raising=False)
    path = Path(tmp_path) / ".env"
    monkeypatch.setattr(credential_store, "env_file", path)
    db = Database(tmp_path / "credentials-api.db")
    app = create_app(db=db, autostart=False)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/settings/ebay",
                json={
                    "client_id": "Test-App-PRD-12345678-abcd",
                    "client_secret": "super-secret-value-123",
                    "environment": "production",
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert "super-secret-value-123" not in json.dumps(body)
            assert body["credentials"]["configured"] is True
            assert body["settings"]["environment"]["ebay_configured"] is True
            health = client.get("/api/health").json()
            assert health["ebay_configured"] is True
            assert health["ebay_setup"]["credential_source"] == "local .env"
            invalid = client.post(
                "/api/settings/ebay",
                json={
                    "client_id": "bad key with spaces",
                    "client_secret": "super-secret-value-123",
                    "environment": "production",
                },
            )
            assert invalid.status_code == 422
            removed = client.delete("/api/settings/ebay")
            assert removed.status_code == 200
            assert removed.json()["credentials"]["configured"] is False
    finally:
        credential_store.clear_ebay()
        db.close()
