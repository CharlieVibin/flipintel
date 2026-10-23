"""Focused regression tests for the parts most likely to create bad deals."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.collectors.base import BaseCollector, CollectorError, run_queries
from app.collectors.ebay import EbayCollector
from app.db import Database
from app.identification import identify_listing
from app.main import create_app
from app.scanner import Scanner
from app.utils import iso
from app.valuation import ValuationConfig, ValuationEngine


def test_accessories_and_games_are_not_consoles():
    for title in (
        "Gulikit Steam Deck Oled joystick",
        "6in1 steam deck",
        "PS5 console faceplates",
        "Empty Nintendo switch box",
        "Lego world nintendo switch",
        "Brand new Nintendo switch turbo boost racing no offers",
    ):
        result = identify_listing(title)
        assert result.category != "console" or result.confidence == 0
        assert result.product_key is None
    for title in (
        "Controllers for ps4 , slim and pro 2pcs",
        "Otterbox iphone 17 pro max",
        "3 phone screen protectors iphone 13 mini",
        "Apple iPhone 15 Pro Max box",
        "MSI Stealth A16 RTX 5090 gaming laptop",
    ):
        result = identify_listing(title)
        if "MSI" in title:
            assert result.category == "laptop"
        else:
            assert result.product_key is None


def test_complete_pc_is_not_a_loose_gpu():
    for title in (
        "AMD ryzen 7 rtx 3050",
        "I5 10th Gen RTX 3050 16gb RAM",
        # Real listings that slipped through as loose GPUs because "gaming
        # computer PC desktop" and compact "Ryzen 5600X" were not recognised.
        "High Spec Gaming Computer PC Desktop | Ryzen 5600X | 32GB RAM | RTX 5060",
        "Horizon Gaming Desktop Tower PC Black GeForce RTX 1650, 8GB DDR4, 256GB SDD",
    ):
        result = identify_listing(title)
        assert result.category == "pc", title
        assert result.kind == "pc", title
        assert result.product_key.startswith("pc:"), title

    compact = identify_listing("AMD Ryzen 5600X")
    assert compact.product_key == "cpu:amd:ryzen-5-5600x"
    assert compact.product_name == "AMD Ryzen 5 5600X"


def test_console_titles_still_match():
    assert identify_listing("Nintendo Switch").category == "console"
    assert identify_listing("Xbox series x").category == "console"
    assert identify_listing("Sony PlayStation 5 Disc Console").category == "console"
    assert identify_listing("Sony PS4 Pro console").product_key == "console:ps4-pro"
    assert identify_listing("PS5 with controller").product_key == "console:ps5"
    bundle = identify_listing("PS5 with extra controller")
    assert bundle.product_key == "console:ps5"
    assert bundle.is_bundle is True
    portal = identify_listing("Ps5 portal")
    assert portal.product_key == "console:playstation-portal"
    assert portal.product_name == "PlayStation Portal"
    # A refresh rate alone cannot identify a monitor's size or resolution.
    vague_monitor = identify_listing("gaming monitor 240hz")
    assert vague_monitor.product_key is None
    for game in (
        "The Crew 2 Xbox one",
        "UFC 3 Xbox One",
        "FC27 PS5 Disc edition",
        "Sony Marvel Wolverine PS5 PlayStation 5 Pro Enhanced",
        "Ps5 undisputed",
        "Xbox Series X One Tony Hawk's Pro Skater 1 & 2",
    ):
        result = identify_listing(game)
        assert result.product_key is None, game
        assert "console_game" in result.flags, game
    for accessory in (
        "Xbox one kinect",
        "Xbox one Kinect sensor",
        "iPhone 11 Pro Max Charging Coil",
        "IPHONE 16 pro max phonecase",
        "Apple MacBook Air 13\" 2020 A2179 WiFi Bluetooth Antenna Assembly OEM",
        "Apple MacBook Air 15 inch Case2024 2023 Release M3 A3114 M2 A2941 Chip",
    ):
        result = identify_listing(accessory)
        assert result.product_key is None, accessory
        assert "accessory_or_box" in result.flags, accessory
    multi = identify_listing("iPhone 14 13 12 11 XS XR X Pro Max Mini 8 7 6S 6 Plus 5")
    assert multi.product_key is None
    assert "multiple_models" in multi.flags


def test_generation_and_configuration_keys():
    gpu = identify_listing("NVIDIA RTX 4070 12GB graphics card")
    assert gpu.product_key == "gpu:rtx-4070-12gb"
    assert gpu.attrs["vram_gb"] == 12
    assert gpu.attrs["generation"] == "40"
    ram = identify_listing("32GB DDR5 6000MHz 2x16GB desktop RAM")
    assert ram.product_key == "ram:ddr5:32gb:6000:2x16:desktop"
    assert ram.attrs["kit_count"] == 2
    ssd = identify_listing("Samsung 990 Pro 2TB NVMe SSD")
    assert ssd.product_key == "ssd:nvme:unspecified:2tb:samsung:990-pro"
    assert ssd.attrs["brand"] == "samsung"
    assert ssd.attrs["model"] == "990-pro"
    hdd = identify_listing("Seagate 4TB internal HDD")
    assert hdd.product_key == "hdd:internal:4tb"


def test_accessory_and_engineering_sample_protection():
    bracket = identify_listing("RTX 4080 compatible bracket")
    assert bracket.product_key is None
    assert "compatibility_accessory" in bracket.flags
    sample = identify_listing("Intel Core i9-13900K engineering sample")
    assert sample.product_key is None
    assert "engineering_sample" in sample.flags
    assert identify_listing("PS5 controller").product_key is None


def test_laptop_and_tablet_variants_do_not_collapse():
    mac = identify_listing("Apple MacBook Pro 14 M3 Pro 18GB 512GB")
    assert mac.product_key == "laptop:macbook-pro-m3-pro-14in"
    assert mac.attrs["screen_in"] == 14
    assert mac.attrs["storage_gb"] == 512
    ipad = identify_listing("iPad Pro 11 M4 256GB")
    assert ipad.product_key == "tablet:ipad-pro-m4-11in-256gb"
    assert ipad.attrs["storage_gb"] == 256
    # Generation wording can appear after colour and other descriptors. It must
    # still be part of the comparable key, otherwise an 8th-gen iPad is valued
    # against 10th-gen and Pro listings.
    old_ipad = identify_listing("iPad rose gold 8th generation 128GB")
    assert old_ipad.product_key == "tablet:ipad-gen8-128gb"
    assert identify_listing("iPad gen 8 128GB").product_key == old_ipad.product_key


def test_phone_shorthand_and_case_brand_traps():
    shorthand = identify_listing("Apple iPhone 12 Pro - 256G - Pacific Blue")
    assert shorthand.product_key == "phone:iphone-12-pro-256gb"
    assert shorthand.attrs["storage_gb"] == 256
    assert "variant_unclear" not in shorthand.flags

    # Live review-queue titles from case and screen-protector sellers that name
    # a handset only for compatibility. They must not enter the comparable pool.
    for title in (
        "BodyGuardz - iPhone 16 Pro Max, Green",
        "Caudabe Paragon - iPhone 16 Pro Max, Glacier",
        "Velvet Caviar - iPhone 16 Pro Max, Floral",
        "Loopy for iPhone 14",
        "iPhone 15 4pcs glass protectors",
    ):
        result = identify_listing(title)
        assert result.product_key is None, title
        assert "accessory_or_box" in result.flags, title


def test_accessories_naming_a_model_do_not_become_the_device():
    # "plus" is a model suffix, not a bundle conjunction. The old rule read
    # "iPhone 16 Plus Phone Cases" as a phone that came with cases.
    for title in (
        "iPhone 16 Plus Phone Cases",
        "iPhone 15 plus case lot",
        "iPhone 14 covers and phone clip",
        "Velvet Caviar Bundle: iPhone 14 Pro Case, MagSafe Ring & Portable Charger",
        "Purple & Gold Google Pixel 8 Phone Case with Rotating Ring Holder Stand",
        "Lenovo Tab A9 Shockproof Case Red & Black with Stand, Brand New",
    ):
        result = identify_listing(title)
        assert result.product_key is None, title
        assert result.confidence == 0, title
        assert "accessory_or_box" in result.flags, title

    # A genuine device that includes a case keeps its identity.
    bundled = identify_listing("iPhone 13 with case and screen protector")
    assert bundled.product_key == "phone:iphone-13"
    assert "accessory_or_box" not in bundled.flags


def test_legacy_capacities_resolve_older_device_variants():
    iphone = identify_listing("iPhone 6 Plus 16gb Unlocked")
    assert iphone.product_key == "phone:iphone-6-plus-16gb"
    assert iphone.attrs["storage_gb"] == 16
    assert "variant_unclear" not in iphone.flags

    ipad = identify_listing("iPad 4th Gen 32GB WiFi")
    assert ipad.product_key == "tablet:ipad-gen4-32gb"
    assert ipad.attrs["storage_gb"] == 32
    assert "variant_unclear" not in ipad.flags

    # A modern SSD capacity is not treated as a legacy phone variant.
    assert identify_listing("iPhone 13 128GB").attrs["storage_gb"] == 128


def test_console_bundles_and_console_accessories_are_separated():
    bundle = identify_listing("Sony PlayStation 5 Console with Controllers and Headset")
    assert bundle.product_key == "console:ps5"
    assert "accessory_or_box" not in bundle.flags

    controller_bundle = identify_listing("Xbox Series X Console & Green Controller")
    assert controller_bundle.product_key == "console:xbox-series-x"

    assert identify_listing("PS5 with 2 games").product_key == "console:ps5"
    assert identify_listing("PS5 game bundle").product_key is None

    kinect = identify_listing("Xbox one Kinect sensor")
    assert kinect.product_key is None
    assert kinect.category == "console"

    # Short game titles name a platform without any hardware cue. A single
    # unexplained word is a game title, not proof of a console for sale.
    for title in ("MW2 ps3", "PS3 Singstar", "Skyrim - PS3", "PS3 games bundle"):
        assert identify_listing(title).product_key is None, title
    assert identify_listing("PS5 disc edition").product_key == "console:ps5"
    assert identify_listing("Xbox 360").product_key == "console:xbox-360"
    assert identify_listing("Sony PS5 Blu-ray drive for digital editions").product_key is None

    # A component-first title only mentions the console in passing.
    ram = identify_listing("RAM 64GB (2x 32GB) Corsair (PS5 Pro exchange)")
    assert ram.product_key is None
    assert ram.kind != "console"

    # A laptop mainboard is a replacement part, not a working laptop.
    mainboard = identify_listing(
        "Dell Latitude 5320 Motherboard i7-1185G7 3.0GHz DDR4 0DFNFK Tested"
    )
    assert mainboard.product_key is None

    # A cracked handset is not valued against good-condition comparables.
    cracked = identify_listing(
        "Samsung Galaxy S22 Ultra - 128GB - Black (Unlocked) - cracked screen but working"
    )
    assert cracked.condition == "fair"
    assert "condition_caution" in cracked.flags

    # A CPU sold with RAM, an SSD and Windows is a complete machine, not a
    # loose processor worth £60.
    workstation = identify_listing(
        "HP 250 G9 Intel Core i5-1235U 8GB RAM 256GB NVMe Windows 11 Pro"
    )
    assert workstation.kind == "pc"
    assert "complete_system" in workstation.flags


def test_working_with_issues_is_faulty_not_a_bargain():
    result = identify_listing(
        'Apple MacBook Air 13" 2019 - 8GB RAM / 128GB SSD - Working With Issues #112'
    )
    assert result.is_faulty is True
    assert result.condition == "faulty"
    assert "faulty_or_parts" in result.flags

    # "No issues" is a positive condition statement, not a fault.
    clean = identify_listing("MacBook Air 2019 no issues, fully working")
    assert clean.is_faulty is False


def _listing(db: Database, *, external_id: str, title: str, price: float,
             key: str = "gpu:rtx-4070", kind: str = "gpu") -> int:
    now = iso()
    return db.execute(
        """
        INSERT INTO listings(
            marketplace, external_id, url, title, price, condition_norm,
            listing_kind, category, product_key, product_name,
            identification_confidence, first_seen, last_seen, status, created_at, updated_at
        ) VALUES ('vinted', ?, ?, ?, ?, 'good', ?, ?, ?, ?, 0.93, ?, ?, 'active', ?, ?)
        """,
        (
            external_id, f"https://www.vinted.co.uk/items/{external_id}", title, price,
            kind, kind, key, "NVIDIA GeForce RTX 4070", now, now, now, now,
        ),
    ).lastrowid


def _add_listing(db: Database, marketplace: str, external_id: str, title: str, price: float,
                 key: str = "gpu:rtx-4070-12gb", kind: str = "gpu") -> int:
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


def test_implausibly_cheap_item_is_flagged_not_a_deal(tmp_path):
    db = Database(tmp_path / "valuation.db")
    try:
        for index in range(5):
            _listing(db, external_id=f"comp-{index}", title=f"RTX 4070 comp {index}", price=300)
        candidate_id = _listing(db, external_id="cheap", title="RTX 4070 bargain", price=60)
        candidate = db.query_one("SELECT * FROM listings WHERE id=?", (candidate_id,))
        assert candidate is not None
        candidate["flags_list"] = []
        result = ValuationEngine(db).evaluate(candidate, ValuationConfig.from_db(db))
        assert result["is_deal"] == 0
        assert any("manual review" in reason.lower() for reason in result["reasons"])
    finally:
        db.close()


def test_ebay_browse_item_parser():
    db = Database(":memory:")
    collector = EbayCollector(db)
    try:
        item = collector._parse_item(
            {
                "title": "NVIDIA RTX 4070 Founders Edition",
                "itemId": "v1|123|0",
                "itemWebUrl": "https://www.ebay.co.uk/itm/123?campid=1",
                "price": {"value": "450.00", "currency": "GBP"},
                "shippingOptions": [{"shippingCost": {"value": "5.00"}}],
                "condition": "Used",
                "buyingOptions": ["FIXED_PRICE"],
                "itemLocation": {"city": "London", "country": "GB"},
                "seller": {"username": "seller"},
            },
            "rtx 4070",
        )
        assert item is not None
        assert item.price == 450.0
        assert item.shipping == 5.0
        assert item.url == "https://www.ebay.co.uk/itm/123"
        assert item.marketplace == "ebay"
    finally:
        asyncio.run(collector.close())
        db.close()


def test_failed_scan_returns_the_error(tmp_path):
    class BrokenCollector(BaseCollector):
        marketplace = "broken"
        label = "Broken"

        async def search_query(self, query, max_pages=1):
            raise CollectorError("simulated parser failure", "parse")

    db = Database(tmp_path / "failure.db")
    collector = BrokenCollector(db)
    try:
        outcome = asyncio.run(run_queries(collector, ["gpu"]))
        assert outcome.status == "failed"
        assert "simulated parser failure" in (outcome.error or "")
        assert outcome.items == []
    finally:
        asyncio.run(collector.close())
        db.close()


def test_api_health_and_empty_feed(tmp_path):
    db = Database(tmp_path / "api.db")
    app = create_app(db=db, autostart=False)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        body = health.json()
        assert body["status"] == "ok"
        assert {source["marketplace"] for source in body["scanner"]["sources"]} == {
            "vinted", "gumtree", "ebay", "facebook"
        }
        feed = client.get("/api/deals")
        assert feed.status_code == 200
        assert feed.json()["deals"] == []


def test_sold_evidence_produces_a_higher_confidence_valuation(tmp_path):
    db = Database(tmp_path / "sold.db")
    try:
        for index in range(5):
            _add_listing(db, "vinted", f"ask-{index}", f"RTX 4070 12GB ask {index}", 300)
        candidate_id = _add_listing(db, "gumtree", "cheap", "RTX 4070 12GB bargain", 250)
        app = create_app(db=db, autostart=False)
        with TestClient(app) as client:
            for index in range(5):
                response = client.post(
                    "/api/comps",
                    json={
                        "product_key": "gpu:rtx-4070-12gb",
                        "title": f"RTX 4070 12GB completed sale {index}",
                        "price": 450,
                        "condition": "good",
                        "postage": 5,
                        "source_id": f"sold-{index}",
                    },
                )
                assert response.status_code == 200, response.text
            detail = client.get(f"/api/deals/{candidate_id}").json()
            assert detail["valuation_method"] == "sold_comps"
            assert detail["confidence"] > 0.75
            assert detail["breakdown"]["sold_count"] == 5
            assert detail["is_deal"] is True
            evidence = client.get("/api/comps?product_key=gpu:rtx-4070-12gb").json()
            assert evidence["summary"]["manual_count"] == 5
            first_id = evidence["comps"][0]["id"]
            assert client.delete(f"/api/comps/{first_id}").status_code == 200
            assert client.get("/api/comps?product_key=gpu:rtx-4070-12gb").json()["summary"]["manual_count"] == 4
    finally:
        db.close()


def test_asking_only_evidence_is_labelled_not_capped_at_63_percent(tmp_path):
    """The old engine multiplied every asking-only estimate down to <= 0.63.

    That ceiling was arithmetic, not uncertainty. Evidence is now disclosed via
    ``valuation_basis`` and the descriptive evidence level, and the internal
    evidence score is free to reflect a genuinely strong asking sample.
    """
    db = Database(tmp_path / "asking.db")
    try:
        for index in range(6):
            _add_listing(db, "vinted", f"ask-{index}", f"RTX 4070 12GB ask {index}", 300)
        for index in range(6):
            _add_listing(db, "gumtree", f"g-ask-{index}", f"RTX 4070 12GB ask {index}", 305)
        candidate_id = _add_listing(db, "gumtree", "cheap", "RTX 4070 12GB bargain", 200)
        ValuationEngine(db).refresh(limit=100)
        row = db.query_one("SELECT * FROM deals WHERE listing_id=?", (candidate_id,))
        assert row is not None
        assert row["valuation_method"] == "live_asking_comps"
        assert row["valuation_basis"] == "asking_only"
        assert row["evidence_level"] == "moderate"
        # No completed sale exists, yet the evidence score is no longer pinned
        # to the old 0.63 ceiling.
        assert row["confidence"] > 0.63
        assert row["confidence"] == row["evidence_score"]
        notes = " ".join(__import__("json").loads(row["breakdown"])["valuation_notes"]).lower()
        assert "no verified completed sale" in notes
        assert "asking-price estimate" in notes
        assert row["rank_score"] is not None
        assert row["identity_level"] in {"limited", "moderate", "strong"}
    finally:
        db.close()


def test_review_queue_is_separate_from_recommendations(tmp_path):
    db = Database(tmp_path / "review.db")
    try:
        for index in range(5):
            _add_listing(db, "vinted", f"ask-{index}", f"RTX 4070 12GB ask {index}", 300)
        cheap_id = _add_listing(db, "gumtree", "cheap", "RTX 4070 12GB bargain", 60)
        app = create_app(db=db, autostart=False)
        with TestClient(app) as client:
            ValuationEngine(db).refresh(limit=100)
            review = client.get("/api/deals?review=1").json()
            assert review["total"] == 1
            assert review["deals"][0]["id"] == cheap_id
            assert review["deals"][0]["needs_review"] is True
            normal = client.get("/api/deals").json()
            assert all(deal["id"] != cheap_id for deal in normal["deals"])
    finally:
        db.close()


def test_manual_comp_upsert_does_not_inflate_evidence(tmp_path):
    db = Database(tmp_path / "upsert.db")
    app = create_app(db=db, autostart=False)
    try:
        with TestClient(app) as client:
            payload = {
                "product_key": "gpu:rtx-4070-12gb",
                "title": "RTX 4070 sale",
                "price": 450,
                "condition": "good",
                "source_id": "ebay-sale-123",
            }
            assert client.post("/api/comps", json=payload).status_code == 200
            payload["price"] = 470
            assert client.post("/api/comps", json=payload).status_code == 200
            result = client.get("/api/comps?product_key=gpu:rtx-4070-12gb").json()
            assert result["summary"]["sold_count"] == 1
            assert result["comps"][0]["price"] == 470
    finally:
        db.close()


def test_resale_channel_and_other_costs_are_configurable(tmp_path):
    db = Database(tmp_path / "settings.db")
    app = create_app(db=db, autostart=False)
    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/settings",
                json={"resale_channel": "vinted", "other_costs": 3.5},
            )
            assert response.status_code == 200, response.text
            fees = response.json()["fees"]
            assert fees["resale_channel"] == "vinted"
            assert fees["other_costs"] == 3.5
            assert client.put("/api/settings", json={"resale_channel": "craigslist"}).status_code == 422
    finally:
        db.close()


def test_cross_marketplace_duplicate_is_flagged_without_merging(tmp_path):
    db = Database(tmp_path / "duplicates.db")
    try:
        first = _add_listing(db, "vinted", "dupe-a", "NVIDIA RTX 4070 12GB graphics card", 450)
        second = _add_listing(db, "gumtree", "dupe-b", "NVIDIA RTX 4070 12GB Graphics Card", 455)
        scanner = Scanner(db, collectors={})
        assert scanner._mark_duplicates() == 1
        rows = db.query("SELECT id, duplicate_of FROM listings ORDER BY id")
        assert rows[0]["duplicate_of"] is None
        assert rows[1]["duplicate_of"] == first
        assert db.scalar("SELECT COUNT(*) FROM listings") == 2
        assert second != first
    finally:
        db.close()
