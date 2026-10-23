"""Category-first discovery regressions: catalogue, parsers, scheduling, eBay."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.categories import (
    CATALOGUES,
    category_by_id,
    category_url,
    categories_for,
)
from app.collectors.base import CategoryPage, RawListing
from app.collectors.ebay import EbayCollector
from app.collectors.gumtree import GumtreeCollector
from app.collectors.vinted import VintedCollector
from app.db import Database
from app.scan_config import save_scan_config
from app.scan_taxonomy import all_leaf_keys
from app.scanner import Scanner
from app.utils import iso


def _listing(db: Database, external_id: str, marketplace: str = "vinted") -> int:
    now = iso()
    return db.execute(
        """
        INSERT INTO listings(
            marketplace, external_id, url, title, price, first_seen, last_seen,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 300, ?, ?, 'active', ?, ?)
        """,
        (
            marketplace, external_id, f"https://www.{marketplace}.co.uk/item/{external_id}",
            "RTX 4070 12GB", now, now, now, now,
        ),
    ).lastrowid


def _raw(external_id: str, category_id: str, price: float = 300) -> RawListing:
    return RawListing(
        marketplace="vinted",
        external_id=external_id,
        url=f"https://www.vinted.co.uk/items/{external_id}",
        title=f"RTX 4070 12GB {external_id}",
        price=price,
        category_id=category_id,
    )


def test_category_catalogues_are_unique_and_urls_are_browsable():
    for marketplace, categories in CATALOGUES.items():
        ids = [category.category_id for category in categories]
        assert ids, marketplace
        assert len(ids) == len(set(ids)), marketplace
        assert all(category.marketplace == marketplace for category in categories)
        if marketplace != "ebay":
            assert all(category.path.startswith("/") for category in categories)

    assert category_by_id("vinted", "3580") is not None
    assert category_by_id("gumtree", "computers-software") is not None
    assert category_by_id("ebay", "139971") is not None
    assert categories_for("facebook") == ()
    assert category_url("gumtree", "computers-software", page=2).endswith(
        "/for-sale/computers-software/uk/page2?sort=newest"
    )
    assert category_url("ebay", "139971") is None


def test_vinted_category_parser_keeps_cards_and_category_id():
    html = """
    <html><body>
      <div data-testid="product-item-id-111">
        <a data-testid="product-item-id-111--overlay-link" href="/items/111-rtx"
           title="NVIDIA RTX 4070 12GB, Brand: NVIDIA, Condition: Good"></a>
        <div data-testid="product-item-id-111--price-text">£300.00</div>
        <div data-testid="product-item-id-111--description-title">NVIDIA</div>
        <div data-testid="product-item-id-111--description-subtitle">Good</div>
        <img data-testid="product-item-id-111--image--img" src="https://img/111.jpg"
             alt="NVIDIA RTX 4070 12GB, Brand: NVIDIA, Condition: Good" />
      </div>
    </body></html>
    """
    collector = VintedCollector.__new__(VintedCollector)
    items = collector.parse_search(html, "category:3580")
    assert len(items) == 1
    item = items[0]
    assert item.external_id == "111"
    assert item.title == "NVIDIA RTX 4070 12GB"
    assert item.price == 300.0
    assert item.condition_raw == "Good"


def test_gumtree_category_parser_pairs_each_card_with_its_own_link():
    html = """
    <html><body>
      <div data-testid="rh-standard-card-listing">
        <a href="/p/graphics-cards/rtx-4070/1234567890">listing</a>
        <article data-testid="rh-standard-card">
          <div data-testid="rh-standard-card-title">NVIDIA RTX 4070 12GB</div>
          <div data-testid="rh-standard-card-price-amount">300</div>
          <div data-testid="rh-standard-card-description">Boxed and working</div>
          <div data-testid="rh-standard-card-info">Manchester</div>
          <figure><img src="https://img/1.jpg" /></figure>
        </article>
      </div>
      <div data-testid="rh-standard-card-listing">
        <a href="/p/graphics-cards/rtx-4080/1234567891">listing</a>
        <article data-testid="rh-standard-card">
          <div data-testid="rh-standard-card-title">NVIDIA RTX 4080 16GB</div>
          <div data-testid="rh-standard-card-price-amount">650</div>
          <div data-testid="rh-standard-card-info">London</div>
        </article>
      </div>
    </body></html>
    """
    collector = GumtreeCollector.__new__(GumtreeCollector)
    items = collector.parse_category(html, "computers-software")
    assert [item.external_id for item in items] == ["1234567890", "1234567891"]
    assert items[0].price == 300.0
    assert items[0].url == "https://www.gumtree.com/p/graphics-cards/rtx-4070/1234567890"
    assert items[0].description == "Boxed and working"
    assert items[0].location == "Manchester"
    assert items[0].category_id == "computers-software"
    assert items[1].url.endswith("1234567891")


def test_ebay_category_search_uses_category_ids_without_a_keyword(tmp_path, monkeypatch):
    db = Database(tmp_path / "ebay-category.db")
    try:
        collector = EbayCollector(db, client=SimpleNamespace())
        collector.min_interval = 0
        calls: list[dict] = []

        class Response:
            status_code = 200
            headers: dict = {}

            def json(self):
                return {"itemSummaries": []}

        async def fake_get(url, params=None, headers=None):
            calls.append(params or {})
            return Response()

        collector.client = SimpleNamespace(get=fake_get)
        collector._owns_client = False

        async def token():
            return "test-token"

        monkeypatch.setattr(collector, "_access_token", token)
        category = category_by_id("ebay", "139971")
        assert category is not None
        page = asyncio.run(collector.category_page(category, page=2))
        assert page.http_status == 200
        params = calls[0]
        assert params["category_ids"] == "139971"
        assert "q" not in params
        assert params["sort"] == "newlyListed"
        assert params["limit"] == 200
        assert params["offset"] == 200
    finally:
        db.close()


class FakeCategoryCollector:
    marketplace = "vinted"
    supports_categories = True
    available = (True, None)
    supports_shipping = True
    backoff_seconds = 0

    def __init__(self, pages: dict[tuple[str, int], CategoryPage]) -> None:
        self.pages = pages
        self.requests: list[tuple[str, int]] = []

    async def category_page(self, category, page: int = 1) -> CategoryPage:
        self.requests.append((category.category_id, page))
        return self.pages.get((category.category_id, page), CategoryPage(page=page, has_more=False))

    async def search(self, queries, max_pages=1):
        raise AssertionError("category mode should only use the keyword supplement")

    async def close(self):
        return None


def _category_scanner(db: Database, collector: FakeCategoryCollector) -> Scanner:
    db.set_settings({
        "discovery_mode": "category",
        "category_requests_per_cycle": "3",
        "keyword_supplement_queries": "0",
        "category_overlap_stop": "0.75",
    })
    save_scan_config(
        db,
        categories=all_leaf_keys(),
        min_price=0,
        max_price=25000,
        marketplaces={"vinted": True, "gumtree": True, "ebay": True},
        active_preset="all-electronics",
    )
    scanner = Scanner(db, collectors={"vinted": collector})
    # The expanded catalogue has many categories; these tests exercise the
    # scheduling mechanics on the laptop branch, so leave the rest out of the
    # first due window instead of depending on catalogue ordering.
    scanner._seed_categories("vinted")
    db.execute(
        "UPDATE category_scans SET next_due_at='2999-01-01T00:00:00+00:00' "
        "WHERE marketplace='vinted' AND category_id<>'3580'"
    )
    db.execute(
        "UPDATE category_scans SET next_due_at=? "
        "WHERE marketplace='vinted' AND category_id='3580'",
        (iso(),),
    )

    async def noop(*args, **kwargs):
        return None

    scanner._enrich_comparables = noop
    scanner._refresh_cex_quotes = noop
    scanner._run_refresh = noop
    scanner._notify = noop
    return scanner


def test_category_scan_stops_on_overlap_after_the_safety_page(tmp_path):
    db = Database(tmp_path / "category-overlap.db")
    try:
        known = _listing(db, "known-1")
        collector = FakeCategoryCollector({
            ("3580", 1): CategoryPage(
                items=[_raw("known-1", "3580"), _raw("new-1", "3580")],
                page=1, has_more=True,
            ),
            ("3580", 2): CategoryPage(
                items=[_raw("known-1", "3580"), _raw("new-1", "3580")],
                page=2, has_more=True,
            ),
        })
        scanner = _category_scanner(db, collector)
        # Only the first catalogue category is due immediately; later ones are
        # staggered by a few seconds so this test exercises one category alone.
        # Mark it as already backfilled so this is an incremental overlap scan.
        scanner._seed_categories("vinted")
        db.execute(
            "UPDATE category_scans SET backfill_done=1, next_due_at=? "
            "WHERE marketplace='vinted' AND category_id='3580'",
            (iso(),),
        )
        outcome = asyncio.run(scanner._scan_categories(collector, manual=False))
        assert outcome.status == "ok", outcome.error
        detail = outcome.detail["categories"][0]
        assert detail["category_id"] == "3580"
        assert detail["pages"] == 2
        assert detail["stop"] == "overlap"
        assert collector.requests == [("3580", 1), ("3580", 2)]
        state = db.query_one(
            "SELECT * FROM category_scans WHERE marketplace='vinted' AND category_id='3580'"
        )
        assert state["backfill_done"] == 1
        assert state["last_new"] == 1
        assert state["last_items"] == 4
        assert state["next_due_at"] is not None
        memberships = db.query(
            "SELECT category_id FROM listing_categories WHERE listing_id=?", (known,)
        )
        assert memberships and memberships[0]["category_id"] == "3580"
    finally:
        db.close()


def test_backfill_phase_scans_one_category_to_the_configured_depth(tmp_path):
    db = Database(tmp_path / "category-capped.db")
    try:
        pages = {
            ("3580", page): CategoryPage(
                items=[_raw(f"capped-{page}-{index}", "3580") for index in range(2)],
                page=page,
                has_more=True,
            )
            for page in range(1, 11)
        }
        collector = FakeCategoryCollector(pages)
        scanner = _category_scanner(db, collector)
        db.set_settings({"category_requests_per_cycle": "10", "category_backfill_pages": "10"})
        scanner._seed_categories("vinted")
        # The target category is due, but the backfill phase must only take one.
        db.execute(
            "UPDATE category_scans SET next_due_at=? "
            "WHERE marketplace='vinted' AND category_id='3580'",
            (iso(),),
        )
        outcome = asyncio.run(scanner._scan_categories(collector, manual=False))
        assert outcome.status == "ok", outcome.error
        assert [category for category, _ in collector.requests] == ["3580"] * 10
        assert [page for _, page in collector.requests] == list(range(1, 11))
        detail = outcome.detail["categories"][0]
        assert detail["stop"] == "page_limit"
        state = db.query_one(
            "SELECT * FROM category_scans WHERE marketplace='vinted' AND category_id='3580'"
        )
        assert state["backfill_done"] == 1
        assert state["coverage"] == "partial"
        assert state["next_page"] == 1
        assert state["mode"] == "incremental"
    finally:
        db.close()


def test_incremental_scan_stops_on_page_one_when_nothing_is_new(tmp_path):
    db = Database(tmp_path / "category-page-one.db")
    try:
        _listing(db, "known-1")
        collector = FakeCategoryCollector({
            ("3580", 1): CategoryPage(
                items=[_raw("known-1", "3580")], page=1, has_more=True,
            ),
        })
        scanner = _category_scanner(db, collector)
        scanner._seed_categories("vinted")
        db.execute(
            "UPDATE category_scans SET backfill_done=1, next_due_at=? "
            "WHERE marketplace='vinted' AND category_id='3580'",
            (iso(),),
        )
        outcome = asyncio.run(scanner._scan_categories(collector, manual=False))
        assert outcome.status == "ok", outcome.error
        assert collector.requests == [("3580", 1)]
        assert outcome.detail["categories"][0]["stop"] == "overlap"
    finally:
        db.close()


def test_reseeding_the_catalogue_does_not_reset_scan_coverage(tmp_path):
    db = Database(tmp_path / "category-reseed.db")
    try:
        scanner = _category_scanner(db, FakeCategoryCollector({}))
        scanner._seed_categories("vinted")
        db.execute(
            "UPDATE category_scans SET coverage='partial', backfill_done=1 "
            "WHERE marketplace='vinted' AND category_id='3580'"
        )
        scanner._seed_categories("vinted")
        state = db.query_one(
            "SELECT coverage, backfill_done FROM category_scans "
            "WHERE marketplace='vinted' AND category_id='3580'"
        )
        assert state["coverage"] == "partial"
        assert state["backfill_done"] == 1
    finally:
        db.close()


def test_category_backfill_finishes_and_marks_clean_coverage(tmp_path):
    db = Database(tmp_path / "category-backfill.db")
    try:
        collector = FakeCategoryCollector({
            ("3580", 1): CategoryPage(
                items=[_raw("a-1", "3580")], page=1, has_more=False,
            ),
        })
        scanner = _category_scanner(db, collector)
        outcome = asyncio.run(scanner._scan_categories(collector, manual=False))
        assert outcome.status == "ok"
        state = db.query_one(
            "SELECT * FROM category_scans WHERE marketplace='vinted' AND category_id='3580'"
        )
        assert state["backfill_done"] == 1
        assert state["coverage"] == "full"
        assert state["mode"] == "incremental"
        snapshot = scanner.category_discovery_snapshot()
        vinted = next(m for m in snapshot["marketplaces"] if m["marketplace"] == "vinted")
        assert vinted["supported"] is True
        assert vinted["scanned_categories"] >= 1
        entry = next(c for c in vinted["categories"] if c["category_id"] == "3580")
        assert entry["state"] == "ok"
        assert entry["new"] == 1
        assert entry["scan_url"].endswith("/catalog/3580-electronics_comp_laptops")
    finally:
        db.close()
