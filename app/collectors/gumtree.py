"""Gumtree UK collector.

Gumtree server-renders search cards and a JSON-LD SearchResultsPage. The JSON-LD
ItemList is authoritative: it carries the canonical URL, title, price, image,
description, condition and a stable listing id for every organic result. Search
pages also contain promoted cards that are absent from the ItemList, so pairing
cards and URLs by position or fuzzy title matching can attach a price to the
wrong listing. That previously corrupted tracked rows when the same URL was
reused for a different card.

The card parser remains only as a fallback for pages whose JSON-LD is missing.
In that fallback URLs must match a card slug closely; unmatched cards are
dropped rather than guessed.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from ..utils import normalise_text, parse_money, token_set
from .base import BaseCollector, CollectorError, RawListing, SearchOutcome, run_queries

_SLUG_ID_RE = re.compile(r"/(\d{6,})(?:[/?#]|$)")

_CONDITION_LABELS = {
    "newcondition": "New",
    "usedcondition": "Used",
    "refurbishedcondition": "Refurbished",
    "damagedcondition": "For parts or not working",
}


class GumtreeCollector(BaseCollector):
    marketplace = "gumtree"
    label = "Gumtree"
    base_url = "https://www.gumtree.com"
    min_interval = 1.8
    supports_shipping = False

    async def search(self, queries: list[str], max_pages: int = 1) -> SearchOutcome:
        return await run_queries(self, queries, max_pages)

    async def search_query(self, query: str, max_pages: int = 1) -> tuple[list[RawListing], int, int | None]:
        url = f"{self.base_url}/uk/srpsearch+{quote_plus(query)}"
        response = await self._request(url, params={"sort": "relevance"})
        items = self.parse_search(response.text, query)
        if not items and not self._is_empty_page(response.text):
            raise CollectorError("Gumtree page loaded but listing data could not be parsed", "parse")
        return items, 1, response.status_code

    @staticmethod
    def _is_empty_page(html: str) -> bool:
        text = normalise_text(BeautifulSoup(html, "lxml").get_text(" ", strip=True)).lower()
        return any(phrase in text for phrase in (
            "no ads found", "no ads for", "0 ads for", "0 ads",
            "no results found", "0 results", "we couldn't find",
        ))

    def parse_search(self, html: str, query: str = "") -> list[RawListing]:
        soup = BeautifulSoup(html, "lxml")
        results = self._parse_jsonld(soup, query)
        if results:
            return results
        return self._parse_cards(soup, query)

    # --- structured data ---------------------------------------------------
    def _parse_jsonld(self, soup: BeautifulSoup, query: str) -> list[RawListing]:
        results: list[RawListing] = []
        seen: set[str] = set()
        for entry in self._jsonld_entries(soup):
            product = entry.get("item") if isinstance(entry, dict) else None
            if not isinstance(product, dict):
                continue
            url = entry.get("url") or product.get("url")
            if not isinstance(url, str) or "/p/" not in url:
                continue
            match = _SLUG_ID_RE.search(url)
            if not match or match.group(1) in seen:
                continue
            title = normalise_text(str(product.get("name") or ""))
            offers = product.get("offers") or {}
            if isinstance(offers, list):
                offers = next((offer for offer in offers if isinstance(offer, dict)), {})
            if not isinstance(offers, dict):
                offers = {}
            price = parse_money(offers.get("price"))
            if not title or price is None or price < 1:
                continue
            image = product.get("image")
            if isinstance(image, list):
                image = image[0] if image else None
            condition_url = str(offers.get("itemCondition") or "").rstrip("/").rsplit("/", 1)[-1].lower()
            description = normalise_text(str(product.get("description") or ""))[:4000]
            seen.add(match.group(1))
            results.append(
                RawListing(
                    marketplace=self.marketplace,
                    external_id=match.group(1),
                    url=url.split("?")[0],
                    title=title,
                    price=price,
                    shipping=0.0,
                    image_url=image if isinstance(image, str) else None,
                    description=description,
                    condition_raw=_CONDITION_LABELS.get(condition_url, ""),
                    location="",
                    seller="",
                    posted_at=None,
                    search_query=query,
                    raw={
                        "source": "jsonld",
                        "position": entry.get("position"),
                        "currency": offers.get("priceCurrency"),
                        "availability": offers.get("availability"),
                        "condition": offers.get("itemCondition"),
                    },
                )
            )
        return results

    @staticmethod
    def _jsonld_entries(soup: BeautifulSoup) -> list[dict[str, Any]]:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("@type") != "SearchResultsPage":
                continue
            item_list = data.get("mainEntity") or {}
            if not isinstance(item_list, dict):
                continue
            entries = [
                entry for entry in item_list.get("itemListElement") or []
                if isinstance(entry, dict)
            ]
            if entries:
                return entries
        return []

    # --- HTML fallback -----------------------------------------------------
    def _parse_cards(self, soup: BeautifulSoup, query: str) -> list[RawListing]:
        cards = soup.select('article[data-testid="rh-standard-card"]')
        urls = self._jsonld_urls(soup)
        if not cards or not urls:
            return []

        results: list[RawListing] = []
        used: set[str] = set()
        for index, card in enumerate(cards):
            title_el = card.select_one('[data-testid="rh-standard-card-title"]')
            price_el = card.select_one('[data-testid="rh-standard-card-price-amount"]')
            if not title_el or not price_el:
                continue
            title = normalise_text(title_el.get_text(" ", strip=True))
            price = parse_money(price_el.get_text(" ", strip=True))
            if not title or not price:
                continue
            url = self._strict_match_url(title, [u for u in urls if u not in used])
            if not url:
                # Promoted cards have no ItemList entry. Never fall back to a
                # positional URL here: one promoted card shifts every later pair.
                continue
            used.add(url)
            match = _SLUG_ID_RE.search(url)
            if not match:
                continue
            description_el = card.select_one('[data-testid="rh-standard-card-description"]')
            location_el = card.select_one('[data-q="tile-location"]')
            img_el = card.select_one("figure img")
            results.append(
                RawListing(
                    marketplace=self.marketplace,
                    external_id=match.group(1),
                    url=url.split("?")[0],
                    title=title,
                    price=price,
                    shipping=0.0,
                    image_url=img_el.get("src") if img_el else None,
                    description=normalise_text(description_el.get_text(" ", strip=True)) if description_el else "",
                    condition_raw="",
                    location=normalise_text(location_el.get_text(" ", strip=True)) if location_el else "",
                    seller="",
                    posted_at=None,
                    search_query=query,
                    raw={"source": "card_fallback", "position": index + 1},
                )
            )
        return results

    @staticmethod
    def _strict_match_url(title: str, urls: list[str]) -> str | None:
        """Match a card title to a canonical URL, requiring most title tokens.

        This is only used when JSON-LD entries are unavailable. A loose match can
        silently attach the wrong URL to a price, so a card with no strong match
        is discarded instead.
        """
        title_tokens = token_set(title) - {"the", "and", "with", "for", "new", "used"}
        if len(title_tokens) < 3:
            return None
        best_url, best_score, best_overlap = None, 0.0, 0
        for url in urls:
            # .../slug/1234567890 — the slug is the segment before the id, not
            # the numeric id itself.
            parts = [part for part in url.split("?")[0].split("/") if part]
            slug = parts[-2] if len(parts) >= 2 else ""
            slug_tokens = token_set(slug.replace("-", " "))
            if not slug_tokens:
                continue
            overlap = len(title_tokens & slug_tokens)
            score = overlap / len(title_tokens)
            if (score, overlap) > (best_score, best_overlap):
                best_url, best_score, best_overlap = url, score, overlap
        return best_url if best_score >= 0.8 and best_overlap >= 3 else None

    @staticmethod
    def _jsonld_urls(soup: BeautifulSoup) -> list[str]:
        urls: list[str] = []
        for entry in GumtreeCollector._jsonld_entries(soup):
            url = entry.get("url")
            if url and isinstance(url, str) and "/p/" in url:
                urls.append(url)
        return urls
