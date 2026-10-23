"""Shared collector infrastructure.

Each marketplace is an isolated module so a broken parser can be fixed without
touching the others. Requests are sequential per host, rate limited, retried
with jitter and short-circuited by a circuit breaker. A failed scan is recorded
as a failure, never as a successful empty scan.
"""
from __future__ import annotations

import asyncio
import random
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import settings
from ..usage import record_api_usage


class CollectorError(Exception):
    def __init__(self, message: str, kind: str = "error", status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.status = status


@dataclass
class RawListing:
    marketplace: str
    external_id: str
    url: str
    title: str
    price: float
    shipping: float = 0.0
    image_url: str | None = None
    description: str = ""
    condition_raw: str = ""
    location: str = ""
    seller: str = ""
    is_auction: bool = False
    posted_at: str | None = None
    search_query: str = ""
    category_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchOutcome:
    marketplace: str
    status: str  # ok | degraded | failed | blocked | not_configured | unavailable
    items: list[RawListing] = field(default_factory=list)
    queries_ok: int = 0
    queries_failed: int = 0
    pages: int = 0
    latency_ms: int = 0
    error: str | None = None
    http_status: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "degraded"}


@dataclass
class CategoryPage:
    items: list[RawListing] = field(default_factory=list)
    page: int = 1
    has_more: bool = False
    http_status: int | None = None
    total: int | None = None


class RobotsGate:
    """Small per-host robots.txt cache. Fail-open on network errors so a
    temporary robots fetch failure does not stop scanning, but honour a
    successful robots.txt that disallows our path."""

    def __init__(self, client: httpx.AsyncClient, user_agent: str) -> None:
        self.client = client
        self.user_agent = user_agent
        self._cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
        self._lock = asyncio.Lock()

    async def allowed(self, url: str) -> tuple[bool, str | None]:
        if not settings.respect_robots:
            return True, None
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        async with self._lock:
            cached = self._cache.get(origin)
            if cached and time.time() - cached[0] < 6 * 3600:
                parser = cached[1]
                if parser is None:
                    return True, None
                return parser.can_fetch(self.user_agent, url), (
                    None if parser.can_fetch(self.user_agent, url)
                    else f"robots.txt at {origin} disallows this path"
                )
            parser: urllib.robotparser.RobotFileParser | None = None
            try:
                response = await self.client.get(origin + "/robots.txt", timeout=10.0)
                if response.status_code == 200:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(response.text.splitlines())
            except Exception:
                parser = None
            self._cache[origin] = (time.time(), parser)
            if parser is None:
                return True, None
            allowed = parser.can_fetch(self.user_agent, url)
            return allowed, None if allowed else f"robots.txt at {origin} disallows this path"


class BaseCollector:
    marketplace = "base"
    label = "Base"
    base_url = ""
    min_interval = 1.2
    supports_shipping = False
    supports_categories = False
    # True only where keyword-free category browsing has been exercised
    # against the live marketplace in this project.
    category_discovery_verified = False

    def __init__(self, db, client: httpx.AsyncClient | None = None) -> None:
        self.db = db
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        self._owns_client = client is None
        self._last_request = 0.0
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self.robots = RobotsGate(self.client, settings.user_agent)
        self.last_http_status: int | None = None
        self.last_latency_ms: int | None = None

    @property
    def available(self) -> tuple[bool, str | None]:
        return True, None

    @property
    def backoff_seconds(self) -> int:
        """Remaining circuit-breaker cooldown, exposed to scanner health."""
        return max(0, int(self._circuit_open_until - time.monotonic()))

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        await asyncio.sleep(random.uniform(0.03, 0.15))
        self._last_request = time.monotonic()

    async def _request(self, url: str, params: dict[str, Any] | None = None, headers: dict | None = None) -> httpx.Response:
        if time.monotonic() < self._circuit_open_until:
            raise CollectorError("Source temporarily paused after repeated failures", "cooldown")
        allowed, reason = await self.robots.allowed(str(httpx.URL(url, params=params)))
        if not allowed:
            raise CollectorError(reason or "Blocked by robots.txt", "robots")
        last_error: Exception | None = None
        for attempt in range(settings.max_retries + 1):
            await self._throttle()
            started = time.monotonic()
            try:
                response = await self.client.get(url, params=params, headers=headers)
                self.last_http_status = response.status_code
                self.last_latency_ms = int((time.monotonic() - started) * 1000)
                record_api_usage(
                    self.db,
                    self.marketplace,
                    status=response.status_code,
                    latency_ms=self.last_latency_ms,
                    error=(
                        f"HTTP {response.status_code}"
                        if response.status_code >= 400 else None
                    ),
                )
                if response.status_code == 403:
                    self._note_failure()
                    raise CollectorError(
                        "Access denied (HTTP 403). The marketplace is blocking this network.",
                        "blocked",
                        403,
                    )
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = min(60.0, float(retry_after)) if (retry_after or "").replace(".", "").isdigit() else 8.0
                    if attempt >= settings.max_retries:
                        raise CollectorError("Rate limited by marketplace (HTTP 429)", "rate_limited", 429)
                    await asyncio.sleep(delay * (attempt + 1))
                    continue
                if response.status_code >= 500:
                    if attempt >= settings.max_retries:
                        raise CollectorError(f"Marketplace server error (HTTP {response.status_code})", "server", response.status_code)
                    await asyncio.sleep(2.0 * (attempt + 1) + random.random())
                    continue
                if response.status_code == 404:
                    raise CollectorError("Search endpoint returned 404 — parser endpoint may need repair", "not_found", 404)
                if response.status_code >= 400:
                    raise CollectorError(f"Request failed (HTTP {response.status_code})", "http", response.status_code)
                self._consecutive_failures = 0
                return response
            except CollectorError as exc:
                last_error = exc
                if exc.kind in {"blocked", "robots", "cooldown", "not_found"}:
                    raise
                if attempt >= settings.max_retries:
                    raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                record_api_usage(
                    self.db, self.marketplace,
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )
                if attempt >= settings.max_retries:
                    self._note_failure()
                    raise CollectorError(f"Network error: {type(exc).__name__}", "network")
                await asyncio.sleep(1.5 * (attempt + 1))
        raise CollectorError(str(last_error or "Request failed"), "unknown")

    def _note_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= 4:
            self._circuit_open_until = time.monotonic() + 15 * 60

    async def search(self, queries: list[str], max_pages: int = 1) -> SearchOutcome:
        raise NotImplementedError

    async def category_page(self, category: Any, page: int = 1) -> CategoryPage:
        """Fetch one keyword-free browse page for a marketplace category."""
        raise NotImplementedError(
            f"{self.marketplace} does not support category browsing"
        )

    def _parse_price(self, text: str | None) -> float | None:
        from ..utils import parse_money
        return parse_money(text)


async def run_queries(collector: BaseCollector, queries: list[str], max_pages: int = 1) -> SearchOutcome:
    available, reason = collector.available
    if not available:
        return SearchOutcome(
            marketplace=collector.marketplace,
            status="not_configured",
            error=reason,
        )
    started = time.monotonic()
    all_items: list[RawListing] = []
    ok = failed = pages = 0
    errors: list[str] = []
    timings: list[dict[str, Any]] = []
    last_http: int | None = None
    for query in queries:
        query_started = time.monotonic()
        try:
            items, page_count, http_status = await collector.search_query(query, max_pages)
            if items:
                ok += 1
                pages += page_count
                all_items.extend(items)
                last_http = http_status or last_http
                timings.append({
                    "query": query, "status": "ok", "items": len(items),
                    "pages": page_count,
                    "ms": int((time.monotonic() - query_started) * 1000),
                })
            else:
                # A 200 with zero parsed rows is a parser/regression signal, not success.
                ok += 1
                pages += page_count
                if page_count == 0:
                    failed += 1
                    errors.append(f"No results parsed for '{query}'")
                timings.append({
                    "query": query, "status": "empty" if page_count else "parse_failed",
                    "items": 0, "pages": page_count,
                    "ms": int((time.monotonic() - query_started) * 1000),
                })
        except CollectorError as exc:
            failed += 1
            errors.append(f"{query}: {exc.message}")
            timings.append({
                "query": query, "status": exc.kind, "items": 0, "pages": 0,
                "ms": int((time.monotonic() - query_started) * 1000),
                "error": exc.message[:240],
            })
            if exc.status:
                last_http = exc.status
            if exc.kind in {"blocked", "robots", "cooldown"}:
                break
        except Exception as exc:  # isolate a single marketplace parser failure
            failed += 1
            errors.append(f"{query}: {type(exc).__name__}: {exc}")
            timings.append({
                "query": query, "status": "parse_failed", "items": 0, "pages": 0,
                "ms": int((time.monotonic() - query_started) * 1000),
                "error": f"{type(exc).__name__}: {exc}"[:240],
            })
    elapsed = int((time.monotonic() - started) * 1000)
    if failed and not ok:
        first_error = errors[0] if errors else "Scan failed"
        status = "blocked" if any("403" in e or "robots" in e.lower() or "blocking" in e.lower() for e in errors) else "failed"
        return SearchOutcome(
            marketplace=collector.marketplace,
            status=status,
            items=[],
            queries_ok=ok,
            queries_failed=failed,
            pages=pages,
            latency_ms=elapsed,
            error=first_error,
            http_status=last_http,
            detail={"errors": errors, "query_timings": timings},
        )
    status = "degraded" if failed else "ok"
    return SearchOutcome(
        marketplace=collector.marketplace,
        status=status,
        items=all_items,
        queries_ok=ok,
        queries_failed=failed,
        pages=pages,
        latency_ms=elapsed,
        error="; ".join(errors[:3]) if errors else None,
        http_status=last_http,
        detail={"errors": errors, "query_timings": timings},
    )
