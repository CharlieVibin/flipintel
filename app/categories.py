"""Keyword-free electronics category catalogue for each marketplace.

Every entry is a category browse endpoint that was checked against the live
marketplace where possible. Keyword discovery still exists for comparables and
miscategorised listings, but it is no longer the primary way listings are found.

Notes:
* Vinted exposes server-rendered ``/catalog/<id>-<slug>`` pages with 96 cards
  per page. The IDs below come from Vinted's public UK catalogue sitemap.
* Gumtree exposes ``/for-sale/<slug>`` category pages with 30 cards per page,
  ``?sort=newest`` ordering and ``/uk/pageN`` pagination.
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
    Category("vinted", "3564", "Computers & accessories", "/catalog/3564-electronics_computers", "2994", priority=3),
    Category("vinted", "3565", "Mobile phones & communication", "/catalog/3565-electronics_phones", "2994", priority=3),
    Category("vinted", "3566", "Audio, headphones & hi-fi", "/catalog/3566-electronics_audio", "2994", priority=2),
    Category("vinted", "3002", "Video games & consoles", "/catalog/3002-electronics_video_games", "2994", priority=3),
    Category("vinted", "3054", "Cameras & accessories", "/catalog/3054-electronics_cameras_accessories", "2994", priority=2),
    Category("vinted", "3568", "TV & home cinema", "/catalog/3568-electronics_tv", "2994", priority=1),
    Category("vinted", "3004", "Wearables", "/catalog/3004-electronics_wearables", "2994", priority=1),
    Category("vinted", "3567", "Tablets & accessories", "/catalog/3567-electronics_tablets_acc", "2994", priority=2),
    Category("vinted", "2995", "Electronics accessories", "/catalog/2995-electronics_accessories", "2994", priority=1),
    # Catch-all branch: covers items filed only at the electronics root. Scanned
    # less often because the branches above already overlap most of it.
    Category("vinted", "2994", "All electronics (catch-all)", "/catalog/2994-electronics", None, priority=0),
)

GUMTREE: tuple[Category, ...] = (
    Category("gumtree", "computers-software", "Computers & software", "/for-sale/computers-software", priority=3),
    Category("gumtree", "phones", "Mobile phones", "/for-sale/phones", priority=3),
    Category("gumtree", "video-games-consoles", "Video games & consoles", "/for-sale/video-games-consoles", priority=3),
    Category("gumtree", "tv-dvd-cameras", "TV, DVD & cameras", "/for-sale/tv-dvd-cameras", priority=2),
    Category("gumtree", "stereos-audio", "Audio & stereo", "/for-sale/stereos-audio", priority=2),
    Category("gumtree", "cameras-studio-equipment", "Cameras & studio equipment", "/for-sale/cameras-studio-equipment", priority=2),
)

EBAY: tuple[Category, ...] = (
    Category("ebay", "177", "PC laptops & netbooks", coverage="partial", priority=3),
    Category("ebay", "179", "PC desktops & all-in-ones", coverage="partial", priority=3),
    Category("ebay", "175673", "Computer components & parts", coverage="partial", priority=2),
    Category("ebay", "165", "Drives, storage & blank media", coverage="partial", priority=2),
    Category("ebay", "171485", "Tablets & eReaders", coverage="partial", priority=2),
    Category("ebay", "139971", "Video game consoles", coverage="partial", priority=3),
    Category("ebay", "139973", "Video games", coverage="partial", priority=2),
    Category("ebay", "54968", "Video game accessories", coverage="partial", priority=1),
    Category("ebay", "9355", "Mobile & smart phones", coverage="partial", priority=3),
    Category("ebay", "9394", "Mobile phone accessories", coverage="partial", priority=1),
    Category("ebay", "31388", "Digital cameras", coverage="partial", priority=2),
    Category("ebay", "11724", "Camcorders", coverage="partial", priority=1),
    Category("ebay", "15200", "Camera, drone & photo accessories", coverage="partial", priority=1),
    Category("ebay", "179697", "Camera drones", coverage="partial", priority=1),
    Category("ebay", "11071", "TVs", coverage="partial", priority=2),
    Category("ebay", "32852", "DVD, Blu-ray & home cinema", coverage="partial", priority=1),
    Category("ebay", "15052", "Portable audio & headphones", coverage="partial", priority=2),
    Category("ebay", "112529", "Headphones", coverage="partial", priority=2),
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
