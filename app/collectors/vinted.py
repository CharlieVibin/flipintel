"""Vinted UK collector. Public catalogue HTML only (no private API calls).

Vinted's catalogue embeds item cards server-side with 96 items per page and
supports a page query parameter. We keep to one page per scheduled cycle and
use a descriptive browser user agent plus conservative pacing.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from ..utils import normalise_text, parse_money
from .base import BaseCollector, CollectorError, RawListing, SearchOutcome, run_queries

_ITEM_ID_RE = re.compile(r"^product-item-id-(\d+)$")


class VintedCollector(BaseCollector):
    marketplace = "vinted"
    label = "Vinted"
    base_url = "https://www.vinted.co.uk"
    min_interval = 1.4
    supports_shipping = True

    def __init__(self, db, client=None) -> None:
        super().__init__(db, client)
        self._session_ready = False

    async def _ensure_session(self) -> None:
        if self._session_ready:
            return
        try:
            response = await self._request(self.base_url + "/")
            if response.status_code < 400:
                self._session_ready = True
        except CollectorError:
            raise
        except Exception:
            # Catalogue pages still work without a warm-up; try the search.
            self._session_ready = True

    async def search(self, queries: list[str], max_pages: int = 1) -> SearchOutcome:
        try:
            await self._ensure_session()
        except CollectorError as exc:
            if exc.kind in {"blocked", "robots", "cooldown"}:
                from .base import SearchOutcome
                return SearchOutcome(
                    marketplace=self.marketplace,
                    status="blocked" if exc.kind != "cooldown" else "failed",
                    error=exc.message,
                    http_status=exc.status,
                )
        return await run_queries(self, queries, max_pages)

    async def search_query(self, query: str, max_pages: int = 1) -> tuple[list[RawListing], int, int | None]:
        items: list[RawListing] = []
        pages = 0
        last_status: int | None = None
        for page in range(1, max(1, max_pages) + 1):
            response = await self._request(
                f"{self.base_url}/catalog",
                params={
                    "search_text": query,
                    "page": page,
                    "order": "newest_first",
                    "currency": "GBP",
                },
            )
            last_status = response.status_code
            page_items = self.parse_search(response.text, query)
            if not page_items and not self._is_empty_page(response.text):
                raise CollectorError("Vinted page loaded but product cards could not be parsed", "parse")
            pages += 1
            items.extend(page_items)
            if len(page_items) < 40:
                break
        return items, pages, last_status

    @staticmethod
    def _is_empty_page(html: str) -> bool:
        text = normalise_text(BeautifulSoup(html, "lxml").get_text(" ", strip=True)).lower()
        return any(phrase in text for phrase in (
            "no items found", "nothing found", "no results", "0 results",
        ))

    def parse_search(self, html: str, query: str = "") -> list[RawListing]:
        soup = BeautifulSoup(html, "lxml")
        results: list[RawListing] = []
        seen: set[str] = set()
        for node in soup.find_all(attrs={"data-testid": _ITEM_ID_RE}):
            testid = node.get("data-testid") or ""
            match = _ITEM_ID_RE.match(testid)
            if not match or match.group(1) in seen:
                continue
            item_id = match.group(1)
            overlay = node.select_one('[data-testid="product-item-id-%s--overlay-link"]' % item_id)
            if not overlay:
                continue
            price_el = node.select_one('[data-testid="product-item-id-%s--price-text"]' % item_id)
            if not price_el:
                continue
            price = parse_money(price_el.get_text(" ", strip=True))
            if not price:
                continue
            title = ""
            raw_title = overlay.get("title") or ""
            if raw_title:
                title = raw_title.split(", Brand:")[0].split(", Condition:")[0]
            if not title:
                image_el = node.select_one('[data-testid="product-item-id-%s--image--img"]' % item_id)
                alt = (image_el.get("alt") or "") if image_el else ""
                title = alt.split(", Brand:")[0].split(", Condition:")[0]
            title = normalise_text(title)
            if not title:
                continue
            brand_el = node.select_one('[data-testid="product-item-id-%s--description-title"]' % item_id)
            condition_el = node.select_one('[data-testid="product-item-id-%s--description-subtitle"]' % item_id)
            image_el = node.select_one('[data-testid="product-item-id-%s--image--img"]' % item_id)
            condition = normalise_text(condition_el.get_text(" ", strip=True)) if condition_el else ""
            brand = normalise_text(brand_el.get_text(" ", strip=True)) if brand_el else ""
            href = overlay.get("href") or ""
            if not href:
                continue
            seen.add(item_id)
            results.append(
                RawListing(
                    marketplace=self.marketplace,
                    external_id=item_id,
                    url="https://www.vinted.co.uk" + href.split("?")[0],
                    title=title,
                    price=price,
                    shipping=0.0,
                    image_url=image_el.get("src") if image_el else None,
                    description=" ".join(x for x in (brand, condition) if x),
                    condition_raw=condition,
                    location="",
                    seller="",
                    posted_at=None,
                    search_query=query,
                    raw={"brand": brand, "condition": condition},
                )
            )
        return results
