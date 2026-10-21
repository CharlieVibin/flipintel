"""eBay UK collector using the official Browse API.

eBay's robots policy explicitly prohibits scraping its HTML and directs
automated access to the API, so no HTML scraper is used. The Browse API is free
with a developer account (add keys in .env). Without keys the source reports
"not configured" rather than pretending to scan.
"""
from __future__ import annotations

import base64
import time
from typing import Any

import httpx

from ..config import settings
from ..utils import parse_money
from .base import BaseCollector, CollectorError, RawListing, SearchOutcome, run_queries


class EbayCollector(BaseCollector):
    marketplace = "ebay"
    label = "eBay UK"
    base_url = "https://www.ebay.co.uk"
    min_interval = 1.0
    supports_shipping = True

    def __init__(self, db, client=None) -> None:
        super().__init__(db, client)
        self._token: str | None = None
        self._token_expires = 0.0
        self._api_base = (
            "https://api.sandbox.ebay.com" if settings.ebay_env == "sandbox"
            else "https://api.ebay.com"
        )

    @property
    def available(self) -> tuple[bool, str | None]:
        if not settings.ebay_configured:
            return False, (
                "eBay needs a free developer API key. Create an app at "
                "developer.ebay.com/my/keys and set EBAY_CLIENT_ID and "
                "EBAY_CLIENT_SECRET in .env."
            )
        return True, None

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires:
            return self._token
        credentials = base64.b64encode(
            f"{settings.ebay_client_id}:{settings.ebay_client_secret}".encode()
        ).decode()
        try:
            response = await self.client.post(
                f"{self._api_base}/identity/v1/oauth2/token",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "client_credentials",
                    "scope": "https://api.ebay.com/oauth/api_scope",
                },
            )
        except httpx.HTTPError as exc:
            raise CollectorError(f"eBay authentication network error: {type(exc).__name__}", "network")
        if response.status_code != 200:
            raise CollectorError(
                f"eBay authentication failed (HTTP {response.status_code}). Check the API keys.",
                "auth",
                response.status_code,
            )
        data = response.json()
        self._token = data.get("access_token")
        if not self._token:
            raise CollectorError("eBay authentication returned no access token", "auth")
        self._token_expires = time.time() + int(data.get("expires_in", 7200)) - 120
        return self._token

    async def search(self, queries: list[str], max_pages: int = 1) -> SearchOutcome:
        return await run_queries(self, queries, max_pages)

    async def search_query(self, query: str, max_pages: int = 1) -> tuple[list[RawListing], int, int | None]:
        token = await self._access_token()
        items: list[RawListing] = []
        pages = 0
        last_status: int | None = None
        limit = 50
        for page in range(max(1, max_pages)):
            offset = page * limit
            if offset >= 200:
                break
            params = {
                "q": query,
                "limit": limit,
                "offset": offset,
                "sort": "newlyListed",
                "filter": ",".join([
                    "buyingOptions:{FIXED_PRICE}",
                    "itemLocationCountry:GB",
                    "deliveryCountry:GB",
                    "priceCurrency:GBP",
                    "price:[5..25000]",
                ]),
            }
            headers = {
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
                "Accept": "application/json",
            }
            try:
                await self._throttle()
                response = await self.client.get(
                    f"{self._api_base}/buy/browse/v1/item_summary/search",
                    params=params,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                raise CollectorError(f"eBay API network error: {type(exc).__name__}", "network")
            last_status = response.status_code
            if response.status_code == 401:
                self._token = None
                raise CollectorError("eBay API rejected the access token", "auth", 401)
            if response.status_code == 429:
                raise CollectorError("eBay API rate limit reached (HTTP 429)", "rate_limited", 429)
            if response.status_code >= 400:
                detail = ""
                try:
                    detail = response.json().get("errors", [{}])[0].get("message", "")
                except Exception:
                    pass
                raise CollectorError(
                    f"eBay API error (HTTP {response.status_code}){': ' + detail if detail else ''}",
                    "http",
                    response.status_code,
                )
            data = response.json()
            summaries = data.get("itemSummaries") or []
            for raw in summaries:
                parsed = self._parse_item(raw, query)
                if parsed:
                    items.append(parsed)
            pages += 1
            if len(summaries) < limit:
                break
        return items, pages, last_status

    def _parse_item(self, raw: dict[str, Any], query: str) -> RawListing | None:
        title = (raw.get("title") or "").strip()
        item_id = str(raw.get("itemId") or "")
        url = raw.get("itemWebUrl") or ""
        price = parse_money((raw.get("price") or {}).get("value"))
        if not title or not item_id or not url or price is None:
            return None
        currency = (raw.get("price") or {}).get("currency")
        if currency and currency != "GBP":
            return None
        shipping = 0.0
        options = raw.get("shippingOptions") or []
        if options:
            cost = (options[0].get("shippingCost") or {})
            shipping = parse_money(cost.get("value")) or 0.0
        image = (raw.get("image") or {}).get("imageUrl")
        location_parts = [raw.get("itemLocation", {}).get("city"),
                          raw.get("itemLocation", {}).get("stateOrProvince"),
                          raw.get("itemLocation", {}).get("country")]
        location = ", ".join(p for p in location_parts if p)
        buying_options = raw.get("buyingOptions") or []
        return RawListing(
            marketplace=self.marketplace,
            external_id=item_id,
            url=url.split("?")[0],
            title=title,
            price=price,
            shipping=shipping,
            image_url=image,
            description=raw.get("condition") or "",
            condition_raw=raw.get("condition") or "",
            location=location,
            seller=(raw.get("seller") or {}).get("username") or "",
            is_auction="AUCTION" in buying_options,
            posted_at=raw.get("itemCreationDate"),
            search_query=query,
            raw={
                "condition_id": raw.get("conditionId"),
                "buying_options": buying_options,
                "seller_feedback": (raw.get("seller") or {}).get("feedbackPercentage"),
            },
        )
