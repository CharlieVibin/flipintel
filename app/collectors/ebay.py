"""eBay UK collector using the official Browse API.

eBay's robots policy explicitly prohibits scraping its HTML and directs
automated access to the API, so no HTML scraper is used. The Browse API is free
with a developer account. Credentials can come from the environment or be saved
from Settings; they are never stored in the database or returned to the browser.

The Browse API exposes active fixed-price listings only. It does not provide
completed-sale transactions, so its data is always treated as asking-price
evidence and is never labelled as a sold comparable.
"""
from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import settings
from ..credentials import credential_store
from ..usage import record_api_usage
from ..utils import iso, parse_money
from .base import BaseCollector, CollectorError, RawListing, SearchOutcome, run_queries

MARKETPLACE_ID = "EBAY_GB"


def _int_header(response: httpx.Response, *names: str) -> int | None:
    for name in names:
        value = response.headers.get(name)
        if value is None:
            continue
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            continue
    return None


def _reset_header(response: httpx.Response) -> str | None:
    value = None
    for name in ("X-RateLimit-Reset", "RateLimit-Reset", "Retry-After"):
        value = response.headers.get(name)
        if value:
            break
    if not value:
        return None
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return value[:80]
    # Header values are normally seconds from now; large values are epoch times.
    if seconds > 10_000_000_000:
        seconds = seconds / 1000
    if seconds > 1_700_000_000:
        return iso(datetime.fromtimestamp(seconds, tz=timezone.utc))
    return iso(datetime.fromtimestamp(time.time() + max(0, seconds), tz=timezone.utc))


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
        self._token_fingerprint: str | None = None
        self._quota_pause_until = 0.0
        self._quota_reason: str | None = None
        self._last_request_at: str | None = None

    @property
    def credentials(self):
        return credential_store.ebay

    @property
    def api_base(self) -> str:
        return (
            "https://api.sandbox.ebay.com"
            if self.credentials.environment == "sandbox"
            else "https://api.ebay.com"
        )

    @property
    def available(self) -> tuple[bool, str | None]:
        credentials = self.credentials
        if not credentials.configured:
            return False, (
                "eBay needs a free developer API key. Open Settings → Data sources "
                "and save EBAY_CLIENT_ID / EBAY_CLIENT_SECRET, or create an app at "
                "developer.ebay.com/my/keys."
            )
        if time.monotonic() < self._quota_pause_until:
            return False, self._quota_reason or "eBay API quota is temporarily exhausted."
        return True, None

    @property
    def backoff_seconds(self) -> int:
        quota = max(0, int(self._quota_pause_until - time.monotonic()))
        return max(quota, super().backoff_seconds)

    async def _access_token(self) -> str:
        credentials = self.credentials
        if (
            self._token
            and time.time() < self._token_expires
            and self._token_fingerprint == credentials.fingerprint
        ):
            return self._token
        encoded = base64.b64encode(
            f"{credentials.client_id}:{credentials.client_secret}".encode()
        ).decode()
        started = time.monotonic()
        try:
            response = await self.client.post(
                f"{self.api_base}/identity/v1/oauth2/token",
                headers={
                    "Authorization": f"Basic {encoded}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "client_credentials",
                    "scope": "https://api.ebay.com/oauth/api_scope",
                },
            )
        except httpx.HTTPError as exc:
            record_api_usage(
                self.db, self.marketplace,
                error=f"OAuth network error: {type(exc).__name__}",
            )
            raise CollectorError(
                f"eBay authentication network error: {type(exc).__name__}", "network"
            )
        record_api_usage(
            self.db, self.marketplace, status=response.status_code,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=None if response.status_code == 200 else f"OAuth HTTP {response.status_code}",
            quota_remaining=_int_header(response, "X-RateLimit-Remaining", "RateLimit-Remaining"),
            quota_limit=_int_header(response, "X-RateLimit-Limit", "RateLimit-Limit"),
            quota_reset_at=_reset_header(response),
        )
        if response.status_code == 401:
            raise CollectorError(
                "eBay rejected these API keys (HTTP 401). Check the client ID and secret, "
                "and confirm the keys belong to the selected environment.",
                "auth",
                401,
            )
        if response.status_code != 200:
            raise CollectorError(
                f"eBay authentication failed (HTTP {response.status_code}). "
                "Check the API keys and environment.",
                "auth",
                response.status_code,
            )
        data = response.json()
        self._token = data.get("access_token")
        if not self._token:
            raise CollectorError("eBay authentication returned no access token", "auth")
        self._token_expires = time.time() + int(data.get("expires_in", 7200)) - 120
        self._token_fingerprint = credentials.fingerprint
        return self._token

    async def search(self, queries: list[str], max_pages: int = 1) -> SearchOutcome:
        return await run_queries(self, queries, max_pages)

    async def probe(self) -> dict[str, Any]:
        """Run a real, read-only connection test against the configured keys."""
        available, reason = self.available
        if not available:
            return {"ok": False, "error": reason, "authenticated": False}
        started = time.monotonic()
        try:
            items, pages, status = await self.search_query("graphics card", max_pages=1)
        except CollectorError as exc:
            return {
                "ok": False,
                "authenticated": False,
                "error": exc.message,
                "kind": exc.kind,
                "http_status": exc.status,
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        return {
            "ok": True,
            "authenticated": True,
            "environment": self.credentials.environment,
            "marketplace": MARKETPLACE_ID,
            "currency": "GBP",
            "sample_count": len(items),
            "pages": pages,
            "http_status": status,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "note": "Connection test only; results were not inserted into the deal feed.",
        }

    async def search_query(self, query: str, max_pages: int = 1) -> tuple[list[RawListing], int, int | None]:
        if time.monotonic() < self._quota_pause_until:
            raise CollectorError(self._quota_reason or "eBay quota pause", "rate_limited")
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
                "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
                "Accept": "application/json",
            }
            started = time.monotonic()
            try:
                await self._throttle()
                response = await self.client.get(
                    f"{self.api_base}/buy/browse/v1/item_summary/search",
                    params=params,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                record_api_usage(
                    self.db, self.marketplace,
                    error=f"Browse network error: {type(exc).__name__}",
                )
                raise CollectorError(f"eBay API network error: {type(exc).__name__}", "network")
            last_status = response.status_code
            self._last_request_at = iso()
            remaining = _int_header(response, "X-RateLimit-Remaining", "RateLimit-Remaining")
            quota_limit = _int_header(response, "X-RateLimit-Limit", "RateLimit-Limit")
            reset_at = _reset_header(response)
            record_api_usage(
                self.db, self.marketplace,
                status=response.status_code,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=None if response.status_code < 400 else f"HTTP {response.status_code}",
                quota_remaining=remaining,
                quota_limit=quota_limit,
                quota_reset_at=reset_at,
            )
            if remaining is not None and remaining <= 5:
                self._quota_pause_until = time.monotonic() + (15 * 60 if remaining <= 0 else 5 * 60)
                self._quota_reason = (
                    "eBay API quota is exhausted; the source will retry automatically."
                    if remaining <= 0
                    else f"eBay API quota is low ({remaining} calls remaining); pausing briefly."
                )
            if response.status_code == 401:
                self._token = None
                raise CollectorError("eBay API rejected the access token", "auth", 401)
            if response.status_code == 429:
                retry_after = _reset_header(response)
                self._quota_pause_until = time.monotonic() + 5 * 60
                self._quota_reason = (
                    f"eBay API rate limit reached (HTTP 429). Retrying after {retry_after or 'a short pause'}."
                )
                raise CollectorError("eBay API rate limit reached (HTTP 429)", "rate_limited", 429)
            if response.status_code >= 400:
                detail = ""
                try:
                    detail = response.json().get("errors", [{}])[0].get("message", "")
                except Exception:
                    pass
                raise CollectorError(
                    f"eBay API error (HTTP {response.status_code})"
                    f"{': ' + detail if detail else ''}",
                    "http",
                    response.status_code,
                )
            try:
                data = response.json()
            except ValueError:
                raise CollectorError("eBay API returned a non-JSON response", "parse", response.status_code)
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
        price_data = raw.get("price") or {}
        price = parse_money(price_data.get("value"))
        if not title or not item_id or not url or price is None:
            return None
        from urllib.parse import urlparse

        parsed_url = urlparse(str(url))
        if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname not in {
            "www.ebay.co.uk", "ebay.co.uk",
        }:
            return None
        # Never import a non-GBP or non-fixed-price result through a parser bug
        # or an API filter regression.
        if price_data.get("currency") != "GBP":
            return None
        buying_options = raw.get("buyingOptions") or []
        if buying_options and "FIXED_PRICE" not in buying_options:
            return None
        location_data = raw.get("itemLocation") or {}
        country = str(location_data.get("country") or "").strip().upper()
        if country and country not in {"GB", "UK", "UNITED KINGDOM"}:
            return None

        shipping = 0.0
        options = raw.get("shippingOptions") or []
        shipping_costs: list[float] = []
        for option in options:
            cost = option.get("shippingCost") or {}
            currency = cost.get("currency")
            if currency and currency != "GBP":
                return None
            value = parse_money(cost.get("value"))
            if value is not None:
                shipping_costs.append(value)
        if shipping_costs:
            shipping = min(shipping_costs)

        image = (raw.get("image") or {}).get("imageUrl")
        if not image:
            thumbnails = raw.get("thumbnailImages") or []
            if thumbnails and isinstance(thumbnails[0], dict):
                image = thumbnails[0].get("imageUrl")
        if image:
            try:
                image_url = urlparse(str(image))
            except ValueError:
                image = None
            else:
                if image_url.scheme not in {"http", "https"}:
                    image = None

        seller = raw.get("seller") or {}
        seller_name = seller.get("username") or ""
        location_parts = [
            location_data.get("city"),
            location_data.get("stateOrProvince"),
            location_data.get("country"),
        ]
        location = ", ".join(p for p in location_parts if p)
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
            seller=seller_name,
            is_auction="AUCTION" in buying_options,
            posted_at=raw.get("itemCreationDate"),
            search_query=query,
            raw={
                "condition_id": raw.get("conditionId"),
                "buying_options": buying_options,
                "seller_feedback": seller.get("feedbackPercentage"),
                "seller_feedback_score": seller.get("feedbackScore"),
                "seller_account_type": seller.get("sellerAccountType"),
                "seller_registration": seller.get("registrationDate"),
                "currency": price_data.get("currency"),
                "marketplace": MARKETPLACE_ID,
                "item_country": country or None,
            },
        )
