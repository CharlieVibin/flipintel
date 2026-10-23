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
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import settings
from ..credentials import credential_store
from ..usage import record_api_usage
from ..utils import iso, parse_money
from .base import BaseCollector, CategoryPage, CollectorError, RawListing, SearchOutcome, run_queries

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
    supports_categories = True
    # Browse category search is implemented from eBay's documented contract,
    # but this workspace has no eBay keys, so it is not marked verified.
    category_discovery_verified = False
    def _browse_filter(self) -> str:
        """Browse filter with the live purchase-budget window.

        eBay supports a server-side GBP price range. The scanner still applies
        the exact total-cost rule because listed postage is added separately.
        """
        low = max(1.0, float(getattr(self, "scan_price_min", 0) or 0))
        high = float(getattr(self, "scan_price_max", 0) or 25000)
        high = max(low, min(25000.0, high))
        return ",".join([
            "buyingOptions:{FIXED_PRICE}",
            "itemLocationCountry:GB",
            "deliveryCountry:GB",
            "priceCurrency:GBP",
            f"price:[{int(low) if low.is_integer() else low}.."
            f"{int(high) if high.is_integer() else high}]",
        ])
    # Browse API has no documented ending-time-window filter, so auctions are
    # requested sorted by "endingSoonest" and filtered to the window locally.
    # This is the documented contract, but it has not been verified live here.
    AUCTION_FILTER = ",".join([
        "buyingOptions:{AUCTION}",
        "itemLocationCountry:GB",
        "deliveryCountry:GB",
        "priceCurrency:GBP",
        "price:[1..25000]",
    ])

    def _auction_filter(self) -> str:
        """Auction filter with the same live purchase-budget window.

        The current bid is the purchase price at discovery time. Postage is
        still checked by the scanner's exact total-cost rule because eBay does
        not fold it into the bid. The class constant remains the unbudgeted
        fallback for callers that do not set ``scan_price_max``.
        """
        high = float(getattr(self, "scan_price_max", 0) or 0)
        if high <= 0:
            return self.AUCTION_FILTER
        low = max(1.0, float(getattr(self, "scan_price_min", 0) or 0))
        high = max(low, min(25000.0, high))
        return ",".join([
            "buyingOptions:{AUCTION}",
            "itemLocationCountry:GB",
            "deliveryCountry:GB",
            "priceCurrency:GBP",
            f"price:[{int(low) if low.is_integer() else low}.."
            f"{int(high) if high.is_integer() else high}]",
        ])

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

    async def search_auctions(
        self, queries: list[str], ending_within_minutes: int = 30, max_queries: int = 3
    ) -> SearchOutcome:
        """Auctions ending inside the next window, newest ending first.

        Only auctions are requested. Anything ending outside the window is
        discarded before it can enter the feed, so the scanner is not rechecking
        auctions that end hours from now.
        """
        available, reason = self.available
        if not available:
            return SearchOutcome(
                marketplace=self.marketplace, status="not_configured", error=reason
            )
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(minutes=max(5, ending_within_minutes))
        items: list[RawListing] = []
        seen: set[str] = set()
        ok = failed = 0
        scanned = 0
        error_text = None
        for query in queries[: max(1, max_queries)]:
            try:
                raw_items, _count, _status = await self._browse_request(
                    {
                        "q": query,
                        "limit": 200,
                        "sort": "endingSoonest",
                        "filter": self._auction_filter(),
                    },
                    f"auction:{query}",
                )
            except CollectorError as exc:
                failed += 1
                error_text = exc.message
                if exc.kind in {"auth", "rate_limited"}:
                    break
                continue
            ok += 1
            scanned += len(raw_items)
            for item in raw_items:
                if item.external_id in seen:
                    continue
                end = item.auction_end_at
                if not end:
                    continue
                try:
                    when = datetime.fromisoformat(end.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                if when <= now or when > horizon:
                    continue
                seen.add(item.external_id)
                items.append(item)
        status = "ok" if ok and not failed else "degraded" if ok else "failed"
        return SearchOutcome(
            marketplace=self.marketplace,
            status=status,
            items=items,
            queries_ok=ok,
            queries_failed=failed,
            pages=ok,
            error=error_text if failed else None,
            detail={
                "kind": "ending_soon",
                "strategy": "keyword",
                "window_minutes": ending_within_minutes,
                "queries": queries[: max(1, max_queries)],
                "requests": ok,
                "scanned": scanned,
                "in_window": len(items),
            },
        )

    async def search_auction_categories(
        self,
        categories: list[tuple[str, str]],
        *,
        ending_within_minutes: int = 30,
        max_requests: int = 3,
        page_size: int = 200,
    ) -> SearchOutcome:
        """Ending-soon auctions from whole hardware categories.

        Keyword auction searches only see listings whose titles happen to match
        the words. A category search with ``endingSoonest`` sees everything
        ending in that branch, so the same request budget covers far more of the
        market. The window is still enforced locally from the real end time.
        """
        available, reason = self.available
        if not available:
            return SearchOutcome(
                marketplace=self.marketplace, status="not_configured", error=reason
            )
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(minutes=max(5, ending_within_minutes))
        items: list[RawListing] = []
        seen: set[str] = set()
        ok = failed = 0
        requests = 0
        scanned = 0
        error_text = None
        used_categories: list[str] = []
        for category_id, label in categories:
            if requests >= max_requests:
                break
            pages_for_category = 0
            offset = 0
            while requests < max_requests and pages_for_category < 2:
                try:
                    raw_items, _count, _status = await self._browse_request(
                        {
                            "category_ids": str(category_id),
                            "limit": page_size,
                            "offset": offset,
                            "sort": "endingSoonest",
                            "filter": self._auction_filter(),
                        },
                        f"auction-category:{label}",
                    )
                except CollectorError as exc:
                    failed += 1
                    error_text = exc.message
                    if exc.kind in {"auth", "rate_limited"}:
                        break
                    break
                requests += 1
                pages_for_category += 1
                ok += 1
                if label not in used_categories:
                    used_categories.append(label)
                scanned += len(raw_items)
                last_end: datetime | None = None
                for item in raw_items:
                    end = item.auction_end_at
                    if not end:
                        continue
                    try:
                        when = datetime.fromisoformat(end.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    last_end = when
                    if when <= now or when > horizon:
                        continue
                    if item.external_id in seen:
                        continue
                    seen.add(item.external_id)
                    items.append(item)
                # With endingSoonest ordering, a full page whose last end time
                # is still inside the window means more in-window auctions
                # exist. Anything else is the end of the relevant set.
                if len(raw_items) < page_size:
                    break
                if last_end is None or last_end > horizon:
                    break
                offset += page_size
            if failed and not ok:
                break
        status = "ok" if ok and not failed else "degraded" if ok else "failed"
        return SearchOutcome(
            marketplace=self.marketplace,
            status=status,
            items=items,
            queries_ok=ok,
            queries_failed=failed,
            pages=requests,
            error=error_text if failed else None,
            detail={
                "kind": "ending_soon",
                "strategy": "category",
                "window_minutes": ending_within_minutes,
                "categories": used_categories,
                "requests": requests,
                "scanned": scanned,
                "in_window": len(items),
            },
        )

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

    async def get_items(self, item_ids: list[str]) -> tuple[list[RawListing], int]:
        """Re-fetch up to 20 known items by ID.

        Ending-soon discovery covers a rotating slice of the catalogue, so a
        shortlisted auction is not guaranteed to appear in the next category
        pass. Checking the specific IDs is the only way to know its latest bid
        and true end time before alerting. The Browse get_items endpoint accepts
        at most 20 IDs per request.
        """
        ids = [str(item) for item in dict.fromkeys(item_ids) if item][:20]
        if not ids:
            return [], 0
        if time.monotonic() < self._quota_pause_until:
            raise CollectorError(self._quota_reason or "eBay quota pause", "rate_limited")
        token = await self._access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
            "Accept": "application/json",
        }
        started = time.monotonic()
        try:
            await self._throttle()
            response = await self.client.get(
                f"{self.api_base}/buy/browse/v1/item/get_items",
                params={"item_ids": ",".join(ids)},
                headers=headers,
            )
        except httpx.HTTPError as exc:
            record_api_usage(
                self.db, self.marketplace,
                error=f"Browse recheck network error: {type(exc).__name__}",
            )
            raise CollectorError(
                f"eBay recheck network error: {type(exc).__name__}", "network"
            )
        self._last_request_at = iso()
        record_api_usage(
            self.db, self.marketplace,
            status=response.status_code,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=None if response.status_code < 400 else f"HTTP {response.status_code}",
            quota_remaining=_int_header(response, "X-RateLimit-Remaining", "RateLimit-Remaining"),
            quota_limit=_int_header(response, "X-RateLimit-Limit", "RateLimit-Limit"),
            quota_reset_at=_reset_header(response),
        )
        if response.status_code == 429:
            self._quota_pause_until = time.monotonic() + 5 * 60
            raise CollectorError("eBay API rate limit reached (HTTP 429)", "rate_limited", 429)
        if response.status_code >= 400:
            raise CollectorError(
                f"eBay recheck failed (HTTP {response.status_code})", "http", response.status_code
            )
        try:
            payload = response.json()
        except ValueError:
            raise CollectorError("eBay recheck returned non-JSON", "parse", response.status_code)
        items = [
            parsed for parsed in (
                self._parse_item(raw, "auction-recheck")
                for raw in payload.get("items") or []
            ) if parsed
        ]
        return items, response.status_code

    async def _browse_request(
        self, params: dict[str, Any], query_label: str
    ) -> tuple[list[RawListing], int, int]:
        if time.monotonic() < self._quota_pause_until:
            raise CollectorError(self._quota_reason or "eBay quota pause", "rate_limited")
        token = await self._access_token()
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
        items = [
            parsed for parsed in (
                self._parse_item(raw, query_label) for raw in summaries
            ) if parsed
        ]
        return items, len(summaries), response.status_code

    async def search_query(self, query: str, max_pages: int = 1) -> tuple[list[RawListing], int, int | None]:
        items: list[RawListing] = []
        pages = 0
        last_status: int | None = None
        limit = 50
        for page in range(max(1, max_pages)):
            offset = page * limit
            if offset >= 200:
                break
            page_items, count, last_status = await self._browse_request(
                {
                    "q": query,
                    "limit": limit,
                    "offset": offset,
                    "sort": "newlyListed",
                    "filter": self._browse_filter(),
                },
                query,
            )
            items.extend(page_items)
            pages += 1
            if count < limit:
                break
        return items, pages, last_status

    async def category_page(self, category, page: int = 1) -> CategoryPage:
        """Keyword-free eBay Browse search for one non-top-level category.

        eBay requires a keyword for top-level categories, so the catalogue uses
        documented leaf/branch IDs. Browse allows one category ID per request.
        """
        limit = 200
        offset = max(0, page - 1) * limit
        if offset >= 10_000:
            return CategoryPage(page=page, has_more=False)
        items, count, status = await self._browse_request(
            {
                "category_ids": str(category.category_id),
                "limit": limit,
                "offset": offset,
                "sort": "newlyListed",
                "filter": self._browse_filter(),
            },
            f"category:{category.category_id}",
        )
        for item in items:
            item.category_id = str(category.category_id)
            item.raw = {**item.raw, "category_id": str(category.category_id)}
        return CategoryPage(
            items=items,
            page=page,
            has_more=count >= limit,
            http_status=status,
        )

    def _parse_item(self, raw: dict[str, Any], query: str) -> RawListing | None:
        title = (raw.get("title") or "").strip()
        item_id = str(raw.get("itemId") or "")
        url = raw.get("itemWebUrl") or ""
        buying_options = raw.get("buyingOptions") or []
        is_auction = "AUCTION" in buying_options
        if buying_options and not ({"FIXED_PRICE", "AUCTION"} & set(buying_options)):
            return None
        price_data = raw.get("price") or {}
        bid_data = raw.get("currentBidPrice") or {}
        bid_value = parse_money(bid_data.get("value"))
        if is_auction:
            # eBay returns `price: null` for auctions; the current bid is the
            # only purchase figure available and lives in currentBidPrice.
            price = bid_value if bid_value is not None else parse_money(price_data.get("value"))
            currency = bid_data.get("currency") or price_data.get("currency")
        else:
            price = parse_money(price_data.get("value"))
            currency = price_data.get("currency")
        if not title or not item_id or not url or price is None:
            return None
        from urllib.parse import urlparse

        parsed_url = urlparse(str(url))
        if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname not in {
            "www.ebay.co.uk", "ebay.co.uk",
        }:
            return None
        # Never import a non-GBP result through a parser bug or filter change.
        if currency != "GBP":
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
            is_auction=is_auction,
            auction_end_at=(
                str(raw.get("itemEndDate")) if is_auction and raw.get("itemEndDate") else None
            ),
            current_bid=bid_value if is_auction else None,
            bid_count=(
                int(raw.get("bidCount")) if is_auction and raw.get("bidCount") is not None else None
            ),
            min_next_bid=parse_money((raw.get("minimumToBid") or {}).get("value"))
            if is_auction else None,
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
