"""Marketplace category catalogue, derived from the scan taxonomy.

The category IDs are data in :mod:`app.scan_taxonomy`. This module keeps the
small lookup surface used by collectors, diagnostics and tests, and guarantees
that a marketplace branch is never duplicated in the catalogue.
"""
from __future__ import annotations

from .scan_taxonomy import (
    Category,
    MARKETPLACE_CATALOGUES,
    catalogue_for,
)

VINTED: tuple[Category, ...] = catalogue_for("vinted")
GUMTREE: tuple[Category, ...] = catalogue_for("gumtree")
EBAY: tuple[Category, ...] = catalogue_for("ebay")

CATALOGUES: dict[str, tuple[Category, ...]] = {
    "vinted": VINTED,
    "gumtree": GUMTREE,
    "ebay": EBAY,
}


def categories_for(marketplace: str) -> tuple[Category, ...]:
    return CATALOGUES.get(marketplace, ())


def category_by_id(marketplace: str, category_id: str) -> Category | None:
    for category in categories_for(marketplace):
        if category.category_id == str(category_id):
            return category
    return None


def category_url(marketplace: str, category_id: str, page: int = 1) -> str | None:
    """Build a human-readable browse URL for diagnostics (not used by the API)."""
    category = category_by_id(marketplace, category_id)
    if not category or not category.path:
        return None
    base = {
        "vinted": "https://www.vinted.co.uk",
        "gumtree": "https://www.gumtree.com",
    }.get(marketplace)
    if not base:
        return None
    if marketplace == "gumtree" and page > 1:
        return f"{base}{category.path}/uk/page{page}?sort=newest"
    return f"{base}{category.path}"
