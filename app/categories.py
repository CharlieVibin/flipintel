"""Keyword-free electronics category catalogue for each marketplace.

Every entry is a category browse endpoint that was checked against the live
marketplace where possible. Keyword discovery still exists for comparables and
miscategorised listings, but it is no longer the primary way listings are found.

The catalogue deliberately targets device branches, not their parent
"accessories" branches. The previous Vinted entries included the whole of
electronics and audio, which produced thousands of phone cases, game discs,
headphones and unrelated collectibles for every genuine machine. Narrow
subcategories are cheaper to scan and their listings are relevant before the
parser even runs.

Notes:
* Vinted exposes server-rendered ``/catalog/<id>-<slug>`` pages with 96 cards
  per page. The IDs below come from Vinted's public UK catalogue sitemap and
  were checked against their parent pages.
* Gumtree exposes ``/for-sale/<slug>`` category pages with 30 cards per page,
  ``?sort=newest`` ordering and ``/uk/pageN`` pagination. Only its three
  hardware categories are scanned; games, audio, cameras and TVs are not
  valued by this application.
* eBay Browse search accepts a single non-top-level ``category_ids`` value with
  no ``q``. Top-level categories are not used because eBay requires a keyword
  for those. The IDs below are EBAY_GB leaf/branch categories documented by
  eBay; they have not been exercised live because this workspace has no keys.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    marketplace: str
    category_id: str
    label: str
    path: str = ""
    parent_id: str | None = None
    coverage: str = "full"  # full | partial
    priority: int = 1  # higher first when several categories are due

    @property
    def key(self) -> tuple[str, str]:
        return (self.marketplace, self.category_id)


VINTED: tuple[Category, ...] = (
    Category("vinted", "3580", "Laptops", "/catalog/3580-electronics_comp_laptops", "3564", priority=3),
    Category("vinted", "3581", "Desktop PCs", "/catalog/3581-electronics_comp_desktops", "3564", priority=3),
    Category("vinted", "3602", "Graphics cards", "/catalog/3602-electronics_comp_graphicscards", "3582", priority=3),
    Category("vinted", "3599", "Processors", "/catalog/3599-electronics_comp_cpus", "3582", priority=3),
    Category("vinted", "3603", "Memory (RAM)", "/catalog/3603-electronics_comp_ram", "3582", priority=2),
    Category("vinted", "3600", "Motherboards", "/catalog/3600-electronics_comp_motherboards", "3582", priority=2),
    Category("vinted", "3605", "Internal hard drives", "/catalog/3605-electronics_comp_internal_hdd", "3582", priority=1),
    Category("vinted", "3607", "Internal SSDs", "/catalog/3607-electronics_comp_internal_ssd", "3582", priority=2),
    Category("vinted", "3637", "Monitors", "/catalog/3637-electronics_comp_monitors", "3564", priority=2),
    Category("vinted", "3661", "Mobile phones", "/catalog/3661-electronics_phones_mobilephones", "3565", priority=3),
    Category("vinted", "3728", "Tablets", "/catalog/3728-electronics_tab_tablets", "3567", priority=2),
    Category("vinted", "3025", "Consoles", "/catalog/3025-electronics_vg_consoles", "3002", priority=3),
    Category("vinted", "3642", "Routers", "/catalog/3642-electronics_comp_routers", "3594", priority=1),
    Category("vinted", "3643", "Mesh Wi-Fi", "/catalog/3643-electronics_comp_mesh", "3594", priority=1),
    Category("vinted", "3644", "Network switches", "/catalog/3644-electronics_comp_network_switches", "3594", priority=1),
)

GUMTREE: tuple[Category, ...] = (
    Category("gumtree", "computers-software", "Computers & software", "/for-sale/computers-software", priority=3),
    Category("gumtree", "phones", "Mobile phones", "/for-sale/phones", priority=3),
    Category("gumtree", "video-games-consoles", "Video games & consoles", "/for-sale/video-games-consoles", priority=3),
)

EBAY: tuple[Category, ...] = (
    Category("ebay", "177", "PC laptops & netbooks", coverage="partial", priority=3),
    Category("ebay", "179", "PC desktops & all-in-ones", coverage="partial", priority=3),
    Category("ebay", "175673", "Computer components & parts", coverage="partial", priority=2),
    Category("ebay", "165", "Drives, storage & blank media", coverage="partial", priority=2),
    Category("ebay", "171485", "Tablets & eReaders", coverage="partial", priority=2),
    Category("ebay", "139971", "Video game consoles", coverage="partial", priority=3),
    Category("ebay", "9355", "Mobile & smart phones", coverage="partial", priority=3),
)

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
