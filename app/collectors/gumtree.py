"""Gumtree UK collector.

Gumtree server-renders search cards and a JSON-LD ItemList of canonical
listing URLs. Cards do not contain the URL themselves, so the two are paired by
position only when the counts agree (otherwise the page is rejected rather than
attaching the wrong URL to a price).
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from ..utils import normalise_text, parse_money
from .base import BaseCollector, CollectorError, RawListing, SearchOutcome, run_queries

_SLUG_ID_RE = re.compile(r"/(\d{6,})(?:[/?#]|$)")


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
            raise CollectorError("Gumtree page loaded but listing cards could not be parsed", "parse")
        return items, 1, response.status_code

    @staticmethod
    def _is_empty_page(html: str) -> bool:
        text = normalise_text(BeautifulSoup(html, "lxml").get_text(" ", strip=True)).lower()
        return any(phrase in text for phrase in (
            "no ads found", "no results found", "0 results", "we couldn't find",
        ))

    def parse_search(self, html: str, query: str = "") -> list[RawListing]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select('article[data-testid="rh-standard-card"]')
        urls = self._jsonld_urls(soup)
        if not cards or not urls:
            return []

        results: list[RawListing] = []
        unused = list(urls)
        for index, card in enumerate(cards):
            title_el = card.select_one('[data-testid="rh-standard-card-title"]')
            price_el = card.select_one('[data-testid="rh-standard-card-price-amount"]')
            if not title_el or not price_el:
                continue
            title = normalise_text(title_el.get_text(" ", strip=True))
            price = parse_money(price_el.get_text(" ", strip=True))
            if not title or not price:
                continue
            url = self._match_url(title, unused)
            if not url and unused and len(unused) == len(cards) - index:
                url = unused[0]
            if not url:
                continue
            unused.remove(url)
            match = _SLUG_ID_RE.search(url)
            if not match:
                continue
            description_el = card.select_one('[data-testid="rh-standard-card-description"]')
            location_el = card.select_one('[data-q="tile-location"]')
            img_el = card.select_one("figure img")
            image = img_el.get("src") if img_el else None
            description = normalise_text(description_el.get_text(" ", strip=True)) if description_el else ""
            location = normalise_text(location_el.get_text(" ", strip=True)) if location_el else ""
            results.append(
                RawListing(
                    marketplace=self.marketplace,
                    external_id=match.group(1),
                    url=url.split("?")[0],
                    title=title,
                    price=price,
                    shipping=0.0,
                    image_url=image,
                    description=description,
                    condition_raw="",
                    location=location,
                    seller="",
                    posted_at=None,
                    search_query=query,
                    raw={"position": index + 1},
                )
            )
        return results

    @staticmethod
    def _match_url(title: str, urls: list[str]) -> str | None:
        from ..utils import token_set
        title_tokens = token_set(title) - {"the", "and", "with", "for", "new", "used"}
        if not title_tokens:
            return None
        best_url, best_score = None, 0.0
        for url in urls:
            slug = url.rsplit("/", 1)[-1]
            slug = re.sub(r"^\d+", "", slug)
            slug_tokens = token_set(slug.replace("-", " "))
            if not slug_tokens:
                continue
            score = len(title_tokens & slug_tokens) / len(title_tokens)
            if score > best_score:
                best_url, best_score = url, score
        return best_url if best_score >= 0.45 else None

    @staticmethod
    def _jsonld_urls(soup: BeautifulSoup) -> list[str]:
        urls: list[str] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("@type") != "SearchResultsPage":
                continue
            item_list = data.get("mainEntity") or {}
            for entry in item_list.get("itemListElement") or []:
                url = entry.get("url") if isinstance(entry, dict) else None
                if url and isinstance(url, str) and "/p/" in url:
                    urls.append(url)
            if urls:
                break
        return urls
