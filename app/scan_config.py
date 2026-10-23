"""Persistent pre-scan configuration: categories, marketplaces and budget.

The scanner reads this module before every automated request. The dashboard
saves the same structure. Keeping the rules here means the UI cannot enable a
category that the collector does not know how to request, and a saved change
takes effect on the next scan without a restart.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from .scan_taxonomy import (
    BUILTIN_PRESET_BY_SLUG,
    BUILTIN_PRESETS,
    LEAF_KEYS,
    NODE_BY_KEY,
    Preset,
    all_leaf_keys,
    catalogue_for,
    classify_listing,
    default_leaf_keys,
    expand,
    marketplace_coverage,
    tree_payload,
)
from .utils import iso


MAX_BUDGET = 25000.0


def _bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ScanConfiguration:
    confirmed: bool
    confirmed_at: str | None
    active_preset: str
    min_price: float
    max_price: float
    marketplaces: dict[str, bool]
    categories: frozenset[str]
    vinted_buyer_fee_pct: float = 0.05
    vinted_buyer_fee_fixed: float = 0.70
    vinted_shipping: float = 3.49

    @property
    def enabled_count(self) -> int:
        return len(self.categories)

    def allows(self, leaf_key: str | None) -> bool:
        return bool(leaf_key) and leaf_key in self.categories

    def marketplace_enabled(self, marketplace: str) -> bool:
        return bool(self.marketplaces.get(marketplace, False))

    def item_total(self, marketplace: str, price: float, shipping: float = 0.0) -> tuple[float, str]:
        """Purchase total and whether delivery/fees were verified or estimated."""
        price = max(0.0, float(price or 0))
        shipping = max(0.0, float(shipping or 0))
        if marketplace == "vinted":
            # Vinted catalogue cards do not expose the buyer-protection fee or
            # the chosen delivery service. Use the configured defaults and say
            # so, instead of pretending the item price is the total cost.
            fee = round(price * self.vinted_buyer_fee_pct + self.vinted_buyer_fee_fixed, 2)
            total = round(price + self.vinted_shipping + fee, 2)
            return total, "estimated_buyer_fee_and_delivery"
        if marketplace == "ebay":
            if shipping > 0:
                return round(price + shipping, 2), "verified_listed_postage"
            return round(price, 2), "postage_unknown"
        return round(price + shipping, 2), "collection_price"

    def affordable(self, marketplace: str, price: float, shipping: float = 0.0) -> tuple[bool, float, str]:
        total, basis = self.item_total(marketplace, price, shipping)
        if self.min_price > 0 and total < self.min_price:
            return False, total, basis
        if self.max_price > 0 and total > self.max_price:
            return False, total, basis
        return True, total, basis

    def server_price_bounds(self, marketplace: str) -> tuple[float, float]:
        """Item-price bounds sent to the marketplace.

        The local total-cost check remains authoritative because eBay postage
        can vary. These bounds only cut clearly out-of-range cards before they
        are parsed. Vinted's buyer fee and delivery are converted back into an
        item-price window so a £100 total does not hide a £95 item.
        """
        low = max(0.0, self.min_price)
        high = max(0.0, self.max_price)
        if marketplace == "vinted":
            overhead = self.vinted_shipping + self.vinted_buyer_fee_fixed
            low = max(0.0, (low - overhead) / (1 + self.vinted_buyer_fee_pct))
            high = max(0.0, (high - overhead) / (1 + self.vinted_buyer_fee_pct))
        if high <= 0:
            high = MAX_BUDGET
        return round(low, 2), round(high, 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "confirmed": self.confirmed,
            "confirmed_at": self.confirmed_at,
            "active_preset": self.active_preset,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "marketplaces": dict(self.marketplaces),
            "categories": sorted(self.categories),
            "enabled_count": self.enabled_count,
        }


def _row_to_config(db, row: dict[str, Any]) -> ScanConfiguration:
    values = db.get_settings()
    try:
        raw_categories = json.loads(row.get("categories") or "[]")
        if not isinstance(raw_categories, list):
            raw_categories = []
    except (TypeError, ValueError):
        raw_categories = []
    categories = expand(str(item) for item in raw_categories)
    if not categories:
        # An empty list is a valid user choice (scan nothing), but a corrupt or
        # missing payload must not silently turn into a free-for-all. Only the
        # seeded row has a null payload.
        if row.get("categories") in (None, "", "[]") and not row.get("confirmed_at"):
            categories = set(default_leaf_keys())
    return ScanConfiguration(
        confirmed=bool(row.get("confirmed_at")),
        confirmed_at=row.get("confirmed_at"),
        active_preset=str(row.get("active_preset") or "budget-electronics"),
        min_price=float(row.get("min_price") or 0),
        max_price=float(row.get("max_price") or 100),
        marketplaces={
            "vinted": _bool(row.get("enabled_vinted"), True),
            "gumtree": _bool(row.get("enabled_gumtree"), True),
            "ebay": _bool(row.get("enabled_ebay"), True),
        },
        categories=frozenset(categories),
        vinted_buyer_fee_pct=float(values.get("vinted_buyer_fee_pct") or 0.05),
        vinted_buyer_fee_fixed=float(values.get("vinted_buyer_fee_fixed") or 0.70),
        vinted_shipping=float(values.get("vinted_shipping") or 3.49),
    )


def ensure_scan_config(db) -> None:
    row = db.query_one("SELECT id FROM scan_config WHERE id=1")
    if row:
        return
    now = iso()
    db.execute(
        "INSERT INTO scan_config(id, active_preset, min_price, max_price, "
        "enabled_vinted, enabled_gumtree, enabled_ebay, categories, confirmed_at, updated_at) "
        "VALUES (1, ?, 0, 100, 1, 1, 1, ?, NULL, ?)",
        ("budget-electronics", json.dumps(sorted(default_leaf_keys())), now),
    )
    db.set_settings(
        {
            "min_purchase_price": "0",
            "max_purchase_price": "100",
            "scan_config_confirmed": "false",
            "active_scan_preset": "budget-electronics",
        }
    )


def load_scan_config(db) -> ScanConfiguration:
    ensure_scan_config(db)
    row = db.query_one("SELECT * FROM scan_config WHERE id=1")
    if not row:
        ensure_scan_config(db)
        row = db.query_one("SELECT * FROM scan_config WHERE id=1") or {}
    return _row_to_config(db, row)


def save_scan_config(
    db,
    *,
    categories: Iterable[str],
    min_price: float,
    max_price: float,
    marketplaces: dict[str, bool],
    active_preset: str = "custom",
    confirmed: bool = True,
) -> ScanConfiguration:
    enabled = expand(str(item) for item in categories)
    min_price = round(max(0.0, min(float(min_price or 0), MAX_BUDGET)), 2)
    max_price = round(max(0.0, min(float(max_price or 0), MAX_BUDGET)), 2)
    if max_price and min_price > max_price:
        raise ValueError("Minimum purchase price cannot exceed the maximum")
    if max_price == 0:
        max_price = MAX_BUDGET
    now = iso()
    confirmed_at = now if confirmed else None
    db.execute(
        "UPDATE scan_config SET active_preset=?, min_price=?, max_price=?, "
        "enabled_vinted=?, enabled_gumtree=?, enabled_ebay=?, categories=?, "
        "confirmed_at=?, updated_at=? WHERE id=1",
        (
            active_preset or "custom",
            min_price,
            max_price,
            1 if marketplaces.get("vinted", True) else 0,
            1 if marketplaces.get("gumtree", True) else 0,
            1 if marketplaces.get("ebay", True) else 0,
            json.dumps(sorted(enabled)),
            confirmed_at,
            now,
        ),
    )
    db.set_settings(
        {
            "min_purchase_price": str(min_price),
            "max_purchase_price": str(max_price),
            "scan_config_confirmed": "true" if confirmed_at else "false",
            "active_scan_preset": active_preset or "custom",
            "enabled_vinted": "true" if marketplaces.get("vinted", True) else "false",
            "enabled_gumtree": "true" if marketplaces.get("gumtree", True) else "false",
            "enabled_ebay": "true" if marketplaces.get("ebay", True) else "false",
        }
    )
    return load_scan_config(db)


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


def _preset_config(preset: Preset | dict[str, Any]) -> dict[str, Any]:
    if isinstance(preset, Preset):
        return {
            "slug": preset.slug,
            "name": preset.name,
            "description": preset.description,
            "min_price": preset.min_price,
            "max_price": preset.max_price,
            "marketplaces": {name: True for name in preset.marketplaces},
            "categories": sorted(expand(preset.categories)),
            "builtin": True,
        }
    try:
        categories = json.loads(preset.get("categories") or "[]")
    except (TypeError, ValueError):
        categories = []
    return {
        "id": preset.get("id"),
        "slug": preset.get("slug"),
        "name": preset.get("name"),
        "description": preset.get("description") or "",
        "min_price": float(preset.get("min_price") or 0),
        "max_price": float(preset.get("max_price") or 100),
        "marketplaces": {
            "vinted": _bool(preset.get("enabled_vinted"), True),
            "gumtree": _bool(preset.get("enabled_gumtree"), True),
            "ebay": _bool(preset.get("enabled_ebay"), True),
        },
        "categories": sorted(expand(str(item) for item in categories)),
        "builtin": False,
    }


def list_presets(db) -> list[dict[str, Any]]:
    presets = [_preset_config(preset) for preset in BUILTIN_PRESETS]
    for row in db.query("SELECT * FROM scan_presets ORDER BY name COLLATE NOCASE"):
        presets.append(_preset_config(row))
    return presets


def preset_by_slug(db, slug: str) -> dict[str, Any] | None:
    if slug in BUILTIN_PRESET_BY_SLUG:
        return _preset_config(BUILTIN_PRESET_BY_SLUG[slug])
    row = db.query_one("SELECT * FROM scan_presets WHERE slug=?", (slug,))
    return _preset_config(row) if row else None


def _clean_name(name: str) -> str:
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    if not text:
        raise ValueError("Preset name is required")
    if len(text) > 80:
        raise ValueError("Preset name must be 80 characters or fewer")
    return text


def create_custom_preset(
    db,
    *,
    name: str,
    base_slug: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    name = _clean_name(name)
    base = preset_by_slug(db, base_slug) if base_slug else None
    if base is None and config is None:
        raise ValueError("A base preset or an explicit configuration is required")
    source = config or base or {}
    categories = source.get("categories") or []
    if isinstance(categories, str):
        categories = [categories]
    marketplaces = source.get("marketplaces") or {}
    if not isinstance(marketplaces, dict):
        marketplaces = {}
    slug = f"custom-{uuid.uuid4().hex[:10]}"
    now = iso()
    db.execute(
        "INSERT INTO scan_presets(slug, name, description, min_price, max_price, "
        "enabled_vinted, enabled_gumtree, enabled_ebay, categories, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            slug,
            name,
            str(source.get("description") or "Saved custom scan configuration"),
            float(source.get("min_price") or 0),
            float(source.get("max_price") or 100),
            1 if marketplaces.get("vinted", True) else 0,
            1 if marketplaces.get("gumtree", True) else 0,
            1 if marketplaces.get("ebay", True) else 0,
            json.dumps(sorted(expand(str(item) for item in categories))),
            now,
            now,
        ),
    )
    row = db.query_one("SELECT * FROM scan_presets WHERE slug=?", (slug,))
    assert row is not None
    return _preset_config(row)


def update_custom_preset(db, preset_id: int, *, name: str | None = None, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM scan_presets WHERE id=?", (preset_id,))
    if not row:
        return None
    fields: dict[str, Any] = {}
    if name is not None:
        fields["name"] = _clean_name(name)
    if config is not None:
        categories = config.get("categories") or []
        if isinstance(categories, str):
            categories = [categories]
        marketplaces = config.get("marketplaces") or {}
        if not isinstance(marketplaces, dict):
            marketplaces = {}
        fields.update(
            {
                "description": str(config.get("description") or row.get("description") or ""),
                "min_price": float(config.get("min_price") or row.get("min_price") or 0),
                "max_price": float(config.get("max_price") or row.get("max_price") or 100),
                "enabled_vinted": 1 if marketplaces.get("vinted", True) else 0,
                "enabled_gumtree": 1 if marketplaces.get("gumtree", True) else 0,
                "enabled_ebay": 1 if marketplaces.get("ebay", True) else 0,
                "categories": json.dumps(sorted(expand(str(item) for item in categories))),
            }
        )
    if not fields:
        return _preset_config(row)
    fields["updated_at"] = iso()
    assignments = ", ".join(f"{key}=?" for key in fields)
    db.execute(
        f"UPDATE scan_presets SET {assignments} WHERE id=?",
        (*fields.values(), preset_id),
    )
    updated = db.query_one("SELECT * FROM scan_presets WHERE id=?", (preset_id,))
    return _preset_config(updated) if updated else None


def delete_custom_preset(db, preset_id: int) -> bool:
    row = db.query_one("SELECT id FROM scan_presets WHERE id=?", (preset_id,))
    if not row:
        return False
    db.execute("DELETE FROM scan_presets WHERE id=?", (preset_id,))
    return True


# ---------------------------------------------------------------------------
# API payload and coverage
# ---------------------------------------------------------------------------


def config_payload(db, config: ScanConfiguration | None = None) -> dict[str, Any]:
    cfg = config or load_scan_config(db)
    coverage = coverage_report(db, cfg)
    enabled_groups = sum(
        1
        for key, node in NODE_BY_KEY.items()
        if node.parent is None and any(item.startswith(key + "/") for item in cfg.categories)
    )
    return {
        **cfg.as_dict(),
        "categories": sorted(cfg.categories),
        "nodes": tree_payload(cfg.categories),
        "presets": list_presets(db),
        "summary": {
            "enabled_leaves": len(cfg.categories),
            "total_leaves": len(LEAF_KEYS),
            "enabled_groups": enabled_groups,
            "valued_leaves": sum(1 for key in cfg.categories if NODE_BY_KEY.get(key) and NODE_BY_KEY[key].valued),
            "selected_marketplaces": [name for name, on in cfg.marketplaces.items() if on],
        },
        "coverage": coverage,
    }


def coverage_report(db, config: ScanConfiguration | None = None) -> dict[str, Any]:
    cfg = config or load_scan_config(db)
    report = marketplace_coverage(cfg.categories)
    live_rows = db.query(
        "SELECT marketplace, COUNT(*) AS n FROM category_scans GROUP BY marketplace"
    )
    live = {str(row["marketplace"]): int(row["n"] or 0) for row in live_rows}
    live_ids: dict[str, list[str]] = {}
    for row in db.query("SELECT marketplace, category_id FROM category_scans"):
        live_ids.setdefault(str(row["marketplace"]), []).append(str(row["category_id"]))
    for marketplace in ("vinted", "gumtree", "ebay"):
        catalogue = {category.category_id for category in catalogue_for(marketplace)}
        if not cfg.marketplace_enabled(marketplace):
            report[marketplace]["selected_categories"] = 0
            report[marketplace]["selected_ids"] = []
            report["summary"]["total_requests_selected"][marketplace] = 0
        report[marketplace]["live_scan_rows"] = live.get(marketplace, 0)
        report[marketplace]["catalogue_count"] = len(catalogue)
        report[marketplace]["live_unmapped"] = sorted(
            category_id for category_id in live_ids.get(marketplace, []) if category_id not in catalogue
        )
        report[marketplace]["not_yet_scanned"] = sorted(
            category_id for category_id in catalogue if category_id not in set(live_ids.get(marketplace, []))
        )
    report["gaps"] = {
        "unmapped_leaves": report["summary"]["unmapped_keys"],
        "unvalued_leaves": report["summary"]["unvalued_keys"],
        "live_categories_without_taxonomy_mapping": {
            marketplace: report[marketplace]["live_unmapped"]
            for marketplace in ("vinted", "gumtree", "ebay")
            if report[marketplace]["live_unmapped"]
        },
    }
    report["notes"] = [
        "Vinted and Gumtree expose server-side price filters; eBay Browse is given a GBP price filter.",
        "Gumtree branches are broad, so cards are classified locally before storage and enrichment.",
        "Product classes marked unvalued are still collectable when explicitly enabled, but do not produce a resale estimate yet.",
    ]
    return report


# ---------------------------------------------------------------------------
# Public classification helpers used by the scanner
# ---------------------------------------------------------------------------


def scan_leaf_for(ident: Any, title: str, source_category: str | None = None, marketplace: str = "") -> str:
    return classify_listing(ident, title, source_category=source_category, marketplace=marketplace)
