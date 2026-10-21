"""CeX UK price lookup.

CeX is used only as an optional resale anchor. Their public web API is behind
Cloudflare and may refuse access from a given network; that is reported on the
dashboard and never treated as a successful quote. Quotes are cached in SQLite.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from .config import settings
from .db import Database
from .utils import iso, jaccard, token_set, utcnow

CEX_ENDPOINT = "https://wss2.cex.uk.webuy.io/v3/boxes"


class CexPricing:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.client = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://uk.webuy.com",
                "Referer": "https://uk.webuy.com/",
            },
        )
        self.last_status: str = "idle"
        self.last_error: str | None = None
        self._failures = 0
        self._pause_until = 0.0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self.client.aclose()

    async def _fetch(self, query: str) -> tuple[str, list[dict[str, Any]], str | None]:
        if time.monotonic() < self._pause_until:
            return "paused", [], self.last_error or "CeX lookups paused after an access error"
        await asyncio.sleep(random.uniform(0.4, 1.2))
        try:
            response = await self.client.get(CEX_ENDPOINT, params={"q": query})
        except httpx.HTTPError as exc:
            self._note_failure(f"CeX network error: {type(exc).__name__}")
            return "failed", [], self.last_error
        if response.status_code in (401, 403, 429):
            self._note_failure(
                f"CeX blocked the request (HTTP {response.status_code}). "
                "CeX prices are unavailable on this network."
            )
            return "blocked", [], self.last_error
        if response.status_code != 200:
            self._note_failure(f"CeX returned HTTP {response.status_code}")
            return "failed", [], self.last_error
        try:
            payload = response.json()
        except ValueError:
            self._note_failure("CeX returned a non-JSON response")
            return "failed", [], self.last_error
        boxes = self._extract_boxes(payload)
        self._failures = 0
        self.last_status = "ok"
        self.last_error = None
        return "ok", boxes, None

    def _note_failure(self, message: str) -> None:
        self.last_status = "blocked" if "blocked" in message.lower() else "failed"
        self.last_error = message
        self._failures += 1
        if self._failures >= 3:
            self._pause_until = time.monotonic() + 3600

    @staticmethod
    def _extract_boxes(payload: Any) -> list[dict[str, Any]]:
        """Tolerantly find the boxes array in CeX API responses."""
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("boxes", "boxDetails", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                found = CexPricing._extract_boxes(value)
                if found:
                    return found
        if payload.get("boxName") or payload.get("boxId"):
            return [payload]
        return []

    async def refresh_quotes(self, models: list[tuple[str, str]], limit: int = 6) -> dict[str, int]:
        """models: [(product_key, display_name)]. Refreshes at most `limit` stale
        quotes, prioritising models without a quote."""
        if not settings.cex_enabled:
            self.last_status = "disabled"
            return {"ok": 0, "failed": 0}
        async with self._lock:
            ok = failed = 0
            for product_key, name in models[:limit]:
                existing = self.db.query_one(
                    "SELECT fetched_at, status, sell_price FROM external_quotes WHERE product_key = ?",
                    (product_key,),
                )
                if existing and existing["status"] == "ok" and existing.get("sell_price"):
                    # Reuse a good quote for 24h.
                    from .utils import parse_iso
                    fetched = parse_iso(existing["fetched_at"])
                    if fetched and (utcnow() - fetched).total_seconds() < 24 * 3600:
                        continue
                status, boxes, error = await self._fetch(name)
                match = self._best_match(boxes, name)
                sell = cash = None
                title = None
                url = None
                if match:
                    sell = self._number(match, ("sellPrice", "sell_price", "price"))
                    cash = self._number(match, ("cashPrice", "cash_price"))
                    title = match.get("boxName") or match.get("title")
                    box_id = match.get("boxId") or match.get("id")
                    if box_id:
                        url = f"https://uk.webuy.com/product-detail?id={box_id}"
                self.db.execute(
                    """
                    INSERT INTO external_quotes(product_key, source, title, sell_price, cash_price, url, status, fetched_at)
                    VALUES (?, 'cex', ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(product_key) DO UPDATE SET
                        title=excluded.title, sell_price=excluded.sell_price,
                        cash_price=excluded.cash_price, url=excluded.url,
                        status=excluded.status, fetched_at=excluded.fetched_at
                    """,
                    (product_key, title, sell, cash, url, status, iso()),
                )
                if status == "ok" and sell:
                    ok += 1
                else:
                    failed += 1
                if status in {"blocked", "paused"}:
                    break
            return {"ok": ok, "failed": failed}

    @staticmethod
    def _number(item: dict, keys: tuple[str, ...]) -> float | None:
        for key in keys:
            value = item.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return round(float(value), 2)
            if isinstance(value, str):
                try:
                    parsed = float(value.replace("£", "").replace(",", "").strip())
                    if parsed > 0:
                        return round(parsed, 2)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _best_match(boxes: list[dict], name: str) -> dict | None:
        target = token_set(name)
        best = None
        best_score = 0.0
        for box in boxes:
            title = str(box.get("boxName") or box.get("title") or "")
            score = jaccard(target, token_set(title))
            if score > best_score:
                best, best_score = box, score
        return best if best_score >= 0.5 else None

    def health(self) -> dict[str, Any]:
        return {
            "enabled": settings.cex_enabled,
            "status": self.last_status if settings.cex_enabled else "disabled",
            "error": self.last_error,
        }
