"""Pre-scan configuration regressions: taxonomy, persistence and enforcement."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

from app.collectors.base import CategoryPage, RawListing
from app.db import Database
from app.identification import identify_listing
from app.main import create_app
from app.scan_config import (
    create_custom_preset,
    delete_custom_preset,
    list_presets,
    load_scan_config,
    save_scan_config,
    scan_leaf_for,
    update_custom_preset,
)
from app.scan_taxonomy import (
    BUILTIN_PRESET_BY_SLUG,
    LEAVES,
    MARKETPLACE_CATALOGUES,
    all_leaf_keys,
    default_leaf_keys,
    expand,
    group_state,
    mapped_category_ids,
)
from app.scanner import ScanConfigurationRequired, Scanner
from app.utils import iso


def _raw(external_id: str, category_id: str, *, title: str, price: float) -> RawListing:
    return RawListing(
        marketplace="vinted",
        external_id=external_id,
        url=f"https://www.vinted.co.uk/items/{external_id}",
        title=title,
        price=price,
        category_id=category_id,
    )


class FakeCollector:
    marketplace = "vinted"
    label = "Fake Vinted"
    supports_categories = True
    category_discovery_verified = True
    available = (True, None)
    backoff_seconds = 0
    min_interval = 0.0

    def __init__(self, pages: dict[tuple[str, int], CategoryPage]) -> None:
        self.pages = pages
        self.requests: list[tuple[str, int]] = []
        self.scan_price_min = 0.0
        self.scan_price_max = 0.0

    async def category_page(self, category, page: int = 1) -> CategoryPage:
        self.requests.append((category.category_id, page))
        return self.pages.get((category.category_id, page), CategoryPage(page=page, has_more=False))

    async def search(self, queries, max_pages=1):
        raise AssertionError("category mode should not fall back to keywords")

    async def close(self):
        return None


def _scanner(db: Database, collector: FakeCollector) -> Scanner:
    db.set_settings(
        {
            "discovery_mode": "category",
            "category_requests_per_cycle": "10",
            "keyword_supplement_queries": "0",
            "category_overlap_stop": "0.75",
        }
    )
    save_scan_config(
        db,
        categories=["pc-components/graphics-cards", "computers-laptops/windows-laptops"],
        min_price=0,
        max_price=100,
        marketplaces={"vinted": True, "gumtree": True, "ebay": True},
        active_preset="custom",
    )
    scanner = Scanner(db, collectors={"vinted": collector})
    scanner._seed_categories("vinted")
    db.execute(
        "UPDATE category_scans SET next_due_at='2999-01-01T00:00:00+00:00' "
        "WHERE marketplace='vinted' AND category_id NOT IN ('3602', '3580')"
    )
    db.execute(
        "UPDATE category_scans SET next_due_at=? "
        "WHERE marketplace='vinted' AND category_id IN ('3602', '3580')",
        (iso(),),
    )

    async def noop(*args, **kwargs):
        return None

    scanner._enrich_comparables = noop
    scanner._refresh_cex_quotes = noop
    scanner._run_refresh = noop
    scanner._notify = noop
    return scanner


def test_taxonomy_has_every_leaf_mapped_and_presets_expand():
    assert len(LEAVES) >= 100
    assert all(node.mappings for node in LEAVES)
    assert set(MARKETPLACE_CATALOGUES) == {"vinted", "gumtree", "ebay"}
    assert all(MARKETPLACE_CATALOGUES[market] for market in ("vinted", "gumtree", "ebay"))

    all_preset = BUILTIN_PRESET_BY_SLUG["all-electronics"]
    assert set(expand(all_preset.categories)) == set(all_leaf_keys())
    assert set(expand(["pc-components"])) >= {
        "pc-components/graphics-cards",
        "pc-components/processors",
        "pc-components/ssds",
    }
    assert group_state("pc-components", set(default_leaf_keys())) == "all"
    assert group_state("computers-laptops", set(default_leaf_keys())) == "none"
    assert group_state("pc-components", set(expand(["pc-components"]))) == "all"


def test_default_config_is_budget_focused_and_unconfirmed(tmp_path):
    db = Database(tmp_path / "defaults.db")
    try:
        config = load_scan_config(db)
        assert config.confirmed is False
        assert config.max_price == 100
        assert "pc-components/graphics-cards" in config.categories
        assert "peripherals/mice" in config.categories
        assert "peripherals/keyboards" in config.categories
        for disabled in (
            "phones-mobile/iphones",
            "computers-laptops/macbooks",
            "gaming-consoles/nintendo",
            "computers-laptops/gaming-desktops",
            "tablets-displays/drawing-tablets",
        ):
            assert disabled not in config.categories
    finally:
        db.close()


def test_configuration_survives_reopen_and_presets_crud(tmp_path):
    path = tmp_path / "persist.db"
    db = Database(path)
    try:
        saved = save_scan_config(
            db,
            categories=["pc-components/graphics-cards", "storage-media/nas"],
            min_price=10,
            max_price=75,
            marketplaces={"vinted": True, "gumtree": False, "ebay": True},
            active_preset="custom",
        )
        assert saved.confirmed and saved.max_price == 75
        preset = create_custom_preset(db, name="GPU run", base_slug="graphics-cards")
        assert preset["name"] == "GPU run"
        assert preset["categories"] == ["pc-components/graphics-cards"]
        renamed = update_custom_preset(db, preset["id"], name="GPU run 2")
        assert renamed and renamed["name"] == "GPU run 2"
        dup = create_custom_preset(db, name="GPU run 2 copy", base_slug=preset["slug"])
        assert dup["categories"] == preset["categories"]
        assert delete_custom_preset(db, dup["id"]) is True
    finally:
        db.close()

    reopened = Database(path)
    try:
        config = load_scan_config(reopened)
        assert config.confirmed
        assert config.min_price == 10
        assert config.max_price == 75
        assert config.marketplaces == {"vinted": True, "gumtree": False, "ebay": True}
        assert set(config.categories) == {"pc-components/graphics-cards", "storage-media/nas"}
        assert any(p["name"] == "GPU run 2" for p in list_presets(reopened))
    finally:
        reopened.close()


def test_unconfirmed_config_blocks_api_scan_and_shows_red_state(tmp_path):
    db = Database(tmp_path / "api.db")
    app = create_app(db=db, autostart=False)
    try:
        with TestClient(app) as client:
            payload = client.get("/api/scan-config").json()
            assert payload["confirmed"] is False
            assert payload["summary"]["enabled_leaves"] > 0
            assert payload["coverage"]["vinted"]["selected_categories"] > 0
            blocked = client.post("/api/scan", json={})
            assert blocked.status_code == 409
            saved = client.put(
                "/api/scan-config",
                json={
                    "categories": ["pc-components/graphics-cards"],
                    "min_price": 0,
                    "max_price": 80,
                    "marketplaces": {"vinted": True, "gumtree": False, "ebay": True},
                    "active_preset": "custom",
                    "confirmed": True,
                },
            )
            assert saved.status_code == 200, saved.text
            body = saved.json()
            assert body["confirmed"] is True
            assert body["summary"]["enabled_leaves"] == 1
            assert body["coverage"]["gumtree"]["selected_categories"] == 0
            health = client.get("/api/health").json()
            assert health["scan_config"]["confirmed"] is True
            assert health["scan_config"]["max_price"] == 80
    finally:
        db.close()


def test_category_requests_are_limited_to_enabled_branches_and_budget(tmp_path):
    db = Database(tmp_path / "requests.db")
    try:
        collector = FakeCollector(
            {
                ("3602", 1): CategoryPage(
                    items=[
                        _raw("gpu-ok", "3602", title="NVIDIA RTX 4060 8GB graphics card", price=40),
                        _raw("gpu-too-much", "3602", title="NVIDIA RTX 4070 12GB graphics card", price=220),
                    ],
                    page=1,
                    has_more=False,
                ),
                ("3580", 1): CategoryPage(
                    items=[_raw("laptop-off", "3580", title="Dell XPS 13 laptop", price=40)],
                    page=1,
                    has_more=False,
                ),
            }
        )
        scanner = _scanner(db, collector)
        save_scan_config(
            db,
            categories=["pc-components/graphics-cards"],
            min_price=0,
            max_price=100,
            marketplaces={"vinted": True, "gumtree": True, "ebay": True},
            active_preset="custom",
        )
        outcome = asyncio.run(scanner._scan_categories(collector, manual=True))
        assert outcome.status == "ok", outcome.error
        assert [category for category, _ in collector.requests] == ["3602"]
        assert db.scalar("SELECT COUNT(*) FROM listings WHERE external_id='gpu-ok'") == 1
        assert db.scalar("SELECT COUNT(*) FROM listings WHERE external_id='gpu-too-much'") == 0
        assert db.scalar("SELECT COUNT(*) FROM listings WHERE external_id='laptop-off'") == 0
        assert 0 < collector.scan_price_max < 100
    finally:
        db.close()


def test_budget_uses_vinted_total_and_ebay_postage():
    db = Database(Path(tempfile.mkdtemp()) / "budget.db")
    try:
        config = save_scan_config(
            db,
            categories=["pc-components/graphics-cards"],
            min_price=50,
            max_price=100,
            marketplaces={"vinted": True, "gumtree": True, "ebay": True},
            active_preset="custom",
        )
        total, basis = config.item_total("vinted", 90)
        assert basis == "estimated_buyer_fee_and_delivery"
        assert total > 90
        allowed, total, _ = config.affordable("vinted", 40)
        assert allowed is False  # 40 + delivery + fee is below the £50 floor
        allowed, _, _ = config.affordable("ebay", 80, 25)
        assert allowed is False  # listed postage makes the total £105
        low, high = config.server_price_bounds("vinted")
        assert low < 50 and high < 100
    finally:
        db.close()


def test_disabled_marketplace_and_groups_block_scanning(tmp_path):
    db = Database(tmp_path / "disabled.db")
    try:
        collector = FakeCollector({})
        scanner = _scanner(db, collector)
        save_scan_config(
            db,
            categories=["pc-components/graphics-cards"],
            min_price=0,
            max_price=100,
            marketplaces={"vinted": True, "gumtree": False, "ebay": False},
            active_preset="custom",
        )
        outcome = asyncio.run(scanner._scan_categories(collector, manual=True))
        assert outcome.status == "ok"
        assert collector.requests

        # Disabling the parent group removes every descendant, not just the
        # leaf the user happened to name.
        save_scan_config(
            db,
            categories=["peripherals/mice"],
            min_price=0,
            max_price=100,
            marketplaces={"vinted": True, "gumtree": True, "ebay": True},
            active_preset="custom",
        )
        blocked = asyncio.run(scanner._scan_categories(collector, manual=True))
        assert blocked.status == "ok"
        assert mapped_category_ids("vinted", load_scan_config(db).categories) == [
            "3588"
        ]
    finally:
        db.close()


def test_classifier_handles_known_product_classes():
    cases = [
        ("NVIDIA RTX 4070 Super 12GB graphics card", "pc-components/graphics-cards"),
        ("Steam Deck OLED 512GB handheld", "gaming-consoles/steam-deck-handhelds"),
        ("Apple iPhone 13 128GB unlocked", "phones-mobile/iphones"),
        ("Apple MacBook Air M2 8GB 256GB", "computers-laptops/macbooks"),
        ("Logitech G502 gaming mouse", "peripherals/mice"),
        ("Keychron K2 mechanical keyboard", "peripherals/keyboards"),
        ("Apple iPad 10th generation 64GB", "tablets-displays/ipads"),
        ("PlayStation 5 Slim disc console", "gaming-consoles/playstation"),
        ("TP-Link Deco X50 mesh Wi-Fi", "networking-smart-home/mesh"),
        ("Samsung Galaxy S23 256GB", "phones-mobile/samsung"),
    ]
    for title, expected in cases:
        ident = identify_listing(title, "", "vinted")
        leaf = scan_leaf_for(ident, title, "3661" if "phone" in expected else None, "vinted")
        assert leaf == expected, (title, leaf, expected)


def test_start_is_gated_until_configuration_is_saved(tmp_path):
    db = Database(tmp_path / "start.db")

    async def run() -> None:
        scanner = Scanner(db, collectors={})
        scanner.scheduled_enabled = False
        await scanner.start()
        assert scanner._tasks == []
        try:
            await scanner.trigger()
        except ScanConfigurationRequired:
            pass
        else:
            raise AssertionError("unconfirmed configuration allowed a scan")
        save_scan_config(
            db,
            categories=["pc-components/graphics-cards"],
            min_price=0,
            max_price=100,
            marketplaces={"vinted": True, "gumtree": False, "ebay": False},
            active_preset="custom",
        )
        await scanner.apply_scan_config()
        assert scanner.scan_config.confirmed

    try:
        asyncio.run(run())
    finally:
        db.close()
