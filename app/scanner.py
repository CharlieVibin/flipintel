"""Scan orchestration: scheduling, ingestion, dedupe and automatic comparable
enrichment. Scans are per-marketplace and independent; one broken source cannot
stop the others."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from .config import DISCOVERY_QUERIES, MARKETPLACE_LABELS, settings
from .collectors import (
    BaseCollector,
    EbayCollector,
    FacebookCollector,
    GumtreeCollector,
    RawListing,
    SearchOutcome,
    VintedCollector,
)
from .db import Database
from .identification import identify_listing
from .notifications import Notifier
from .pricing_cex import CexPricing
from .utils import hours_ago, iso, json_dumps, normalise_text, parse_iso, utcnow
from .valuation import ValuationEngine

log = logging.getLogger(__name__)

ALLOWED_HOSTS = {
    "vinted": {"www.vinted.co.uk"},
    "gumtree": {"www.gumtree.com"},
    "ebay": {"www.ebay.co.uk"},
    "facebook": {"www.facebook.com"},
}


def interleaved_queries() -> list[tuple[str, str]]:
    """Round-robin the category queries so every cycle sees a spread of
    categories instead of scanning all GPUs at once."""
    categories = list(DISCOVERY_QUERIES.keys())
    rows: list[tuple[str, str]] = []
    depth = max(len(v) for v in DISCOVERY_QUERIES.values())
    for index in range(depth):
        for category in categories:
            values = DISCOVERY_QUERIES[category]
            if index < len(values):
                rows.append((category, values[index]))
    return rows


def search_term_for(key: str, name: str) -> str:
    term = name
    for prefix in ("NVIDIA GeForce ", "AMD Radeon ", "Intel "):
        if term.startswith(prefix):
            term = term[len(prefix):]
    if key.startswith("pc:"):
        term += " gaming pc"
    elif key.startswith("laptop:"):
        term += " laptop"
    return term.strip()


def _valid_url(url: str, marketplace: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    return parsed.hostname in ALLOWED_HOSTS.get(marketplace, set())


class Scanner:
    def __init__(self, db: Database, collectors: dict[str, BaseCollector] | None = None) -> None:
        self.db = db
        self.collectors = collectors or {
            "vinted": VintedCollector(db),
            "gumtree": GumtreeCollector(db),
            "ebay": EbayCollector(db),
            "facebook": FacebookCollector(db),
        }
        self.valuation = ValuationEngine(db)
        self.cex = CexPricing(db)
        self.notifier = Notifier(db)
        self._locks = {name: asyncio.Lock() for name in self.collectors}
        self._refresh_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._running: set[str] = set()
        self._started = False
        self._last_refresh_at: str | None = None
        self._last_refresh_result: dict[str, int] | None = None

    # --- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        # Re-apply the current classifier to stored listings so parser fixes
        # (for example separating a whole PC from its GPU) take effect without
        # deleting the user's collected market history.
        try:
            changed = self._reidentify_existing()
            if changed:
                log.info("Re-identified %d existing listings with the current rules", changed)
                await self._run_refresh(force=True)
        except Exception:
            log.exception("Existing-listing re-identification failed")
        for marketplace in ("vinted", "gumtree", "ebay"):
            self._tasks.append(asyncio.create_task(self._loop(marketplace), name=f"scan-{marketplace}"))
        self._tasks.append(asyncio.create_task(self._maintenance_loop(), name="maintenance"))
        log.info("Scanner started for %s", ", ".join(self.collectors))

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.cex.close()
        await self.notifier.close()
        for collector in self.collectors.values():
            await collector.close()
        self._started = False

    def _reidentify_existing(self) -> int:
        rows = self.db.query(
            "SELECT id, title, description, condition_raw, category, product_key, product_name, "
            "identification_confidence, is_faulty, is_wanted, is_bundle, listing_kind, flags, attrs "
            "FROM listings"
        )
        changed = 0
        for row in rows:
            ident = identify_listing(
                row["title"] or "",
                f'{row["description"] or ""} {row["condition_raw"] or ""}',
            )
            flags_json = json_dumps(ident.flags)
            attrs_json = json_dumps(ident.attrs)
            if (
                row["category"] == ident.category
                and row["product_key"] == ident.product_key
                and row["product_name"] == ident.product_name
                and abs(float(row["identification_confidence"] or 0) - ident.confidence) < 0.001
                and int(row["is_faulty"] or 0) == int(ident.is_faulty)
                and int(row["is_wanted"] or 0) == int(ident.is_wanted)
                and int(row["is_bundle"] or 0) == int(ident.is_bundle)
                and row["listing_kind"] == ident.kind
                and row["flags"] == flags_json
                and row["attrs"] == attrs_json
            ):
                continue
            self.db.execute(
                "UPDATE listings SET category=?, product_key=?, product_name=?, "
                "identification_confidence=?, is_faulty=?, is_wanted=?, is_bundle=?, "
                "listing_kind=?, flags=?, attrs=? WHERE id=?",
                (
                    ident.category, ident.product_key, ident.product_name, ident.confidence,
                    int(ident.is_faulty), int(ident.is_wanted), int(ident.is_bundle),
                    ident.kind, flags_json, attrs_json, row["id"],
                ),
            )
            changed += 1
        return changed

    async def _sleep(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(1.0, seconds))
            return True
        except asyncio.TimeoutError:
            return False

    async def _loop(self, marketplace: str) -> None:
        await self._sleep(random.uniform(2, 8))
        while not self._stop.is_set():
            try:
                if not self.db.setting_bool(f"enabled_{marketplace}", marketplace != "facebook"):
                    self._set_status(marketplace, "paused", next_scan=None)
                    await self._sleep(30)
                    continue
                await self.scan_marketplace(marketplace, manual=False)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Scan loop error for %s", marketplace)
                self._set_status(marketplace, "error", last_error=f"{type(exc).__name__}: {exc}")
            interval = max(60, self.db.setting_int(f"scan_interval_{marketplace}", 300))
            jitter = random.uniform(2, max(3, interval * 0.08))
            self._set_status(marketplace, None, next_scan=iso(utcnow() + timedelta(seconds=interval + jitter)))
            await self._sleep(interval + jitter)

    async def _maintenance_loop(self) -> None:
        while not self._stop.is_set():
            if await self._sleep(3600):
                return
            try:
                self.db.execute(
                    "UPDATE listings SET status='stale' WHERE status='active' AND last_seen < ?",
                    (hours_ago(24 * 14),),
                )
            except Exception:
                log.exception("Stale listing maintenance failed")

    # --- public control -----------------------------------------------------
    async def scan_marketplace(self, marketplace: str, manual: bool = False) -> SearchOutcome | None:
        collector = self.collectors.get(marketplace)
        if not collector:
            return None
        lock = self._locks[marketplace]
        if lock.locked():
            return None
        async with lock:
            self._running.add(marketplace)
            try:
                return await self._scan(collector, manual=manual)
            finally:
                self._running.discard(marketplace)

    async def trigger(self, marketplace: str | None = None) -> list[str]:
        targets = [marketplace] if marketplace else [m for m in ("vinted", "gumtree", "ebay")]
        accepted: list[str] = []
        for target in targets:
            if target not in self.collectors or self._locks[target].locked():
                continue
            asyncio.create_task(self.scan_marketplace(target, manual=True), name=f"manual-{target}")
            accepted.append(target)
        return accepted

    # --- scan ---------------------------------------------------------------
    def _queries_for_cycle(self, marketplace: str) -> list[str]:
        all_queries = interleaved_queries()
        count = max(1, min(12, self.db.setting_int("queries_per_cycle", 4)))
        cursor_key = f"cursor_{marketplace}"
        cursor = self.db.setting_int(cursor_key, 0)
        selected = [all_queries[(cursor + i) % len(all_queries)][1] for i in range(count)]
        self.db.set_settings({cursor_key: str((cursor + count) % len(all_queries))})
        return list(dict.fromkeys(selected))

    async def _scan(self, collector: BaseCollector, manual: bool = False) -> SearchOutcome:
        marketplace = collector.marketplace
        enabled = self.db.setting_bool(f"enabled_{marketplace}", marketplace != "facebook")
        if not enabled:
            self._set_status(marketplace, "paused", last_error=None)
            return SearchOutcome(marketplace=marketplace, status="paused", error="Source paused in settings")

        available, reason = collector.available
        if not available:
            state = "unavailable" if marketplace == "facebook" else "not_configured"
            self._set_status(marketplace, state, last_attempt_at=iso(), last_error=reason, next_scan=None)
            return SearchOutcome(marketplace=marketplace, status=state, error=reason)

        queries = self._queries_for_cycle(marketplace)
        started = utcnow()
        run_id = self.db.execute(
            "INSERT INTO scan_runs(marketplace, started_at, status, queries) VALUES (?, ?, 'running', ?)",
            (marketplace, iso(started), json_dumps(queries)),
        ).lastrowid
        self._set_status(marketplace, "scanning", last_attempt_at=iso(started), last_error=None)

        try:
            outcome = await collector.search(queries, max_pages=1)
        except Exception as exc:
            log.exception("Collector crashed: %s", marketplace)
            outcome = SearchOutcome(
                marketplace=marketplace,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                queries_failed=len(queries),
            )

        found = new = drops = 0
        if outcome.items:
            found, new, drops = self._ingest(outcome.items, marketplace)
        finished = utcnow()
        duration_ms = int((finished - started).total_seconds() * 1000)

        if outcome.status in {"ok", "degraded"}:
            await self._enrich_comparables()
            await self._refresh_cex_quotes()
            await self._run_refresh()
            await self._notify()
            state = "healthy" if outcome.status == "ok" else "degraded"
            self.db.execute(
                "UPDATE marketplace_status SET state=?, last_success_at=?, last_error=?, "
                "consecutive_failures=0, listings_seen=?, last_duration_ms=?, checked_at=? "
                "WHERE marketplace=?",
                (state, iso(finished), outcome.error, found, duration_ms, iso(finished), marketplace),
            )
            run_status = outcome.status
        else:
            state = {
                "blocked": "blocked",
                "not_configured": "not_configured",
                "unavailable": "unavailable",
            }.get(outcome.status, "error")
            failures = (self.db.scalar(
                "SELECT consecutive_failures FROM marketplace_status WHERE marketplace=?", (marketplace,)
            ) or 0) + 1
            self.db.execute(
                "UPDATE marketplace_status SET state=?, last_error=?, consecutive_failures=?, "
                "last_duration_ms=?, checked_at=? WHERE marketplace=?",
                (state, outcome.error, failures, duration_ms, iso(finished), marketplace),
            )
            run_status = outcome.status

        self.db.execute(
            "UPDATE scan_runs SET finished_at=?, status=?, items_found=?, items_new=?, "
            "price_drops=?, duration_ms=?, error=?, detail=? WHERE id=?",
            (
                iso(finished), run_status, found, new, drops, duration_ms,
                outcome.error, json_dumps(outcome.detail), run_id,
            ),
        )
        log.info(
            "Scan %s: %s, %d found, %d new, %d price drops, %dms%s",
            marketplace, run_status, found, new, drops, duration_ms,
            f" ({outcome.error})" if outcome.error else "",
        )
        return outcome

    def _ingest(self, items: list[RawListing], marketplace: str) -> tuple[int, int, int]:
        now = iso()
        seen: set[str] = set()
        found = new = drops = 0
        for item in items:
            if not item.external_id or item.external_id in seen:
                continue
            if not item.title or not item.url or item.price is None or item.price < 1:
                continue
            if not _valid_url(item.url, marketplace):
                continue
            seen.add(item.external_id)
            found += 1
            title = normalise_text(item.title)[:500]
            description = normalise_text(item.description)[:4000]
            ident = identify_listing(title, f"{description} {item.condition_raw}", marketplace)
            existing = self.db.query_one(
                "SELECT id, price, last_price FROM listings WHERE marketplace=? AND external_id=?",
                (marketplace, item.external_id),
            )
            raw_json = json_dumps(item.raw)
            flags_json = json_dumps(ident.flags)
            attrs_json = json_dumps(ident.attrs)
            condition = ident.condition
            if not condition or condition == "unknown":
                raw_condition = normalise_text(item.condition_raw).lower()
                if "new" in raw_condition:
                    condition = "new"
                elif "very good" in raw_condition:
                    condition = "very_good"
                elif "good" in raw_condition:
                    condition = "good"
            if existing:
                old_price = float(existing["price"])
                price_dropped = item.price < old_price - 0.01
                self.db.execute(
                    """
                    UPDATE listings SET
                        url=?, title=?, description=?, image_url=?, price=?, shipping=?,
                        condition_raw=?, condition_norm=?, location=?, seller=?, listing_kind=?,
                        category=?, product_key=?, product_name=?, identification_confidence=?,
                        is_faulty=?, is_wanted=?, is_bundle=?, is_auction=?, posted_at=?,
                        last_seen=?, last_price=?, previous_price=?, price_dropped_at=?,
                        status='active', search_query=?, raw_json=?, flags=?, attrs=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        item.url, title, description, item.image_url, item.price, item.shipping,
                        item.condition_raw, condition, item.location, item.seller, ident.kind,
                        ident.category, ident.product_key, ident.product_name, ident.confidence,
                        int(ident.is_faulty), int(ident.is_wanted), int(ident.is_bundle),
                        int(item.is_auction), item.posted_at, now, item.price,
                        old_price if price_dropped else None,
                        now if price_dropped else None,
                        item.search_query, raw_json, flags_json, attrs_json, now,
                        existing["id"],
                    ),
                )
                if price_dropped:
                    drops += 1
                    self.db.execute(
                        "INSERT INTO price_history(listing_id, price, observed_at) VALUES (?, ?, ?)",
                        (existing["id"], item.price, now),
                    )
                elif abs(item.price - old_price) > 0.01:
                    self.db.execute(
                        "INSERT INTO price_history(listing_id, price, observed_at) VALUES (?, ?, ?)",
                        (existing["id"], item.price, now),
                    )
            else:
                new += 1
                cursor = self.db.execute(
                    """
                    INSERT INTO listings(marketplace, external_id, url, title, description,
                        image_url, price, shipping, currency, condition_raw, condition_norm,
                        location, seller, listing_kind, category, product_key, product_name,
                        identification_confidence, is_faulty, is_wanted, is_bundle, is_auction,
                        posted_at, first_seen, last_seen, last_price, status, search_query,
                        raw_json, flags, attrs, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'GBP', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        marketplace, item.external_id, item.url, title, description,
                        item.image_url, item.price, item.shipping, item.condition_raw, condition,
                        item.location, item.seller, ident.kind, ident.category, ident.product_key,
                        ident.product_name, ident.confidence, int(ident.is_faulty),
                        int(ident.is_wanted), int(ident.is_bundle), int(item.is_auction),
                        item.posted_at, now, now, item.price, item.search_query,
                        raw_json, flags_json, attrs_json, now, now,
                    ),
                )
                self.db.execute(
                    "INSERT INTO price_history(listing_id, price, observed_at) VALUES (?, ?, ?)",
                    (cursor.lastrowid, item.price, now),
                )
        return found, new, drops

    # --- enrichment and refresh --------------------------------------------
    async def _enrich_comparables(self) -> None:
        """Automatically fetch a model-level search for models whose comparable
        set is thin. No user configuration is needed; this is rate limited."""
        candidates = self.db.query(
            """
            SELECT l.product_key, l.listing_kind, l.product_name, COUNT(*) AS n, COUNT(DISTINCT l.marketplace) AS mp
            FROM listings l
            WHERE l.status='active' AND l.product_key IS NOT NULL AND l.last_seen >= ?
              AND l.is_faulty=0 AND l.is_wanted=0
            GROUP BY l.product_key, l.listing_kind
            HAVING n BETWEEN 1 AND 5
            ORDER BY n DESC, l.last_seen DESC
            LIMIT 40
            """,
            (hours_ago(24 * 7),),
        )
        budget = 2
        for row in candidates:
            if budget <= 0:
                break
            key = row["product_key"]
            term = search_term_for(key, row["product_name"] or "")
            if not term:
                continue
            cache = self.db.query_one(
                "SELECT last_fetched_at FROM comp_cache WHERE product_key=? AND marketplace='vinted'",
                (key,),
            )
            if cache:
                fetched = parse_iso(cache["last_fetched_at"])
                if fetched and (utcnow() - fetched).total_seconds() < 6 * 3600:
                    continue
            collector = self.collectors.get("vinted")
            if not collector or not self.db.setting_bool("enabled_vinted", True):
                continue
            try:
                await self.cex_quote_pause_check()
                outcome = await collector.search([term], max_pages=1)
            except Exception:
                log.exception("Comparable enrichment failed for %s", key)
                continue
            self.db.execute(
                "INSERT INTO comp_cache(product_key, marketplace, last_fetched_at, status, item_count) "
                "VALUES (?, 'vinted', ?, ?, ?) ON CONFLICT(product_key, marketplace) DO UPDATE SET "
                "last_fetched_at=excluded.last_fetched_at, status=excluded.status, item_count=excluded.item_count",
                (key, iso(), outcome.status, len(outcome.items)),
            )
            if outcome.items:
                self._ingest(outcome.items, "vinted")
            if outcome.status in {"blocked", "failed"}:
                log.warning("Comparable enrichment source problem: %s", outcome.error)
                break
            budget -= 1

    async def cex_quote_pause_check(self) -> None:
        # Kept as a hook so enrichment pacing can evolve without changing callers.
        return None

    async def _refresh_cex_quotes(self) -> None:
        if not settings.cex_enabled:
            return
        models = self.db.query(
            """
            SELECT product_key, product_name, COUNT(*) AS n FROM listings
            WHERE status='active' AND product_key IS NOT NULL AND is_faulty=0 AND is_wanted=0
              AND last_seen >= ?
            GROUP BY product_key HAVING n >= 2 ORDER BY n DESC LIMIT 3
            """,
            (hours_ago(24 * 7),),
        )
        if not models:
            return
        await self.cex.refresh_quotes(
            [(row["product_key"], row["product_name"] or "") for row in models], limit=3
        )

    async def _run_refresh(self, force: bool = False) -> None:
        async with self._refresh_lock:
            now = utcnow()
            if not force and self._last_refresh_at:
                prev = parse_iso(self._last_refresh_at)
                if prev and (now - prev).total_seconds() < 45:
                    return
            try:
                result = await asyncio.to_thread(self.valuation.refresh)
                self._last_refresh_at = iso(now)
                self._last_refresh_result = result
                log.info("Valuation refresh: %s", result)
            except Exception:
                log.exception("Valuation refresh failed")

    async def _notify(self) -> None:
        try:
            await self.notifier.notify_new_deals()
        except Exception:
            log.exception("Notification dispatch failed")

    # --- status -------------------------------------------------------------
    def _set_status(
        self,
        marketplace: str,
        state: str | None,
        *,
        last_attempt_at: str | None = None,
        last_error: str | None = None,
        next_scan: str | None = None,
    ) -> None:
        fields = ["checked_at=?"]
        params: list[Any] = [iso()]
        if state is not None:
            fields.append("state=?")
            params.append(state)
        if last_attempt_at is not None:
            fields.append("last_attempt_at=?")
            params.append(last_attempt_at)
        if last_error is not None:
            fields.append("last_error=?")
            params.append(last_error)
        if next_scan is not None:
            fields.append("next_scan_at=?")
            params.append(next_scan)
        params.append(marketplace)
        self.db.execute(f"UPDATE marketplace_status SET {', '.join(fields)} WHERE marketplace=?", params)

    def health_snapshot(self) -> dict[str, Any]:
        rows = self.db.query("SELECT * FROM marketplace_status")
        statuses = {row["marketplace"]: row for row in rows}
        sources = []
        for marketplace, collector in self.collectors.items():
            available, reason = collector.available
            enabled = self.db.setting_bool(f"enabled_{marketplace}", marketplace != "facebook")
            row = statuses.get(marketplace, {})
            sources.append(
                {
                    "marketplace": marketplace,
                    "label": MARKETPLACE_LABELS.get(marketplace, marketplace.title()),
                    "enabled": enabled,
                    "available": available,
                    "availability_reason": reason,
                    "state": "paused" if not enabled else row.get("state", "idle"),
                    "running": marketplace in self._running,
                    "last_attempt_at": row.get("last_attempt_at"),
                    "last_success_at": row.get("last_success_at"),
                    "last_error": (None if not enabled else row.get("last_error")),
                    "consecutive_failures": row.get("consecutive_failures", 0),
                    "next_scan_at": None if not enabled else row.get("next_scan_at"),
                    "listings_seen": row.get("listings_seen", 0),
                    "last_duration_ms": row.get("last_duration_ms"),
                    "interval_seconds": self.db.setting_int(f"scan_interval_{marketplace}", 300),
                }
            )
        health = {
            "scanner_running": self._started,
            "sources": sources,
            "cex": self.cex.health(),
            "last_refresh_at": self._last_refresh_at,
            "last_refresh": self._last_refresh_result,
        }
        return health
