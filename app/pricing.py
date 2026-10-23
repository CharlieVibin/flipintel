"""Permanent product-price memory.

Every valuation the engine produces is appended to ``valuation_snapshots`` with
its evidence, so a product researched once is reused across marketplaces instead
of being re-researched per listing. Snapshots are never overwritten: a refresh
adds a new observation and the history stays available for trend work.

A snapshot is fresh for ``history_reuse_days`` (7 by default). After that the
product is reported as stale and the scanner schedules a refresh for it,
prioritising products attached to live deals and ending auctions. Simultaneous
requests for the same product are collapsed by ``pricing_research`` so one
product is never researched twice at once.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from .db import Database
from .utils import iso, parse_iso, utcnow

# A claim older than this is assumed abandoned (the process died mid-request).
CLAIM_MINUTES = 15


class PricingDatabase:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- snapshots ---------------------------------------------------------
    def record(
        self,
        product_key: str,
        kind: str,
        estimate: Any,
        sources: dict[str, int] | None = None,
    ) -> bool:
        """Append one historical snapshot. Returns False for an exact repeat."""
        if not product_key or not kind or estimate is None:
            return False
        resale = getattr(estimate, "resale_good", None)
        if not resale:
            return False
        strategies = getattr(estimate, "strategies", {}) or {}
        latest = self.latest(product_key)
        confidence = round(float(getattr(estimate, "confidence", 0.0) or 0.0), 3)
        if latest and abs(float(latest["resale_good"] or 0) - float(resale)) < 0.01:
            if abs(float(latest["confidence"] or 0) - confidence) < 0.005:
                return False
        self.db.execute(
            """
            INSERT INTO valuation_snapshots(
                product_key, kind, resale_good, resale_low, resale_high,
                confidence, evidence_level, sample_size, sold_count, sources,
                method, refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_key,
                kind,
                round(float(resale), 2),
                getattr(estimate, "low", None),
                getattr(estimate, "high", None),
                confidence,
                getattr(estimate, "evidence_level", "insufficient"),
                int(getattr(estimate, "sample_size", 0) or 0),
                int(getattr(estimate, "sold_count", 0) or 0),
                json.dumps(
                    {
                        "marketplaces": sources or getattr(estimate, "sources", {}) or {},
                        "strategies": strategies,
                        "dispersion": getattr(estimate, "dispersion", None),
                    }
                ),
                getattr(estimate, "method", ""),
                iso(),
            ),
        )
        return True

    def latest(self, product_key: str) -> dict[str, Any] | None:
        """The newest snapshot still valid for this identity.

        A snapshot produced for a product identity that later turned out to be
        wrong is marked invalid rather than deleted: the history stays for audit
        and trend work, but it can never price another listing again.
        """
        return self.db.query_one(
            "SELECT * FROM valuation_snapshots WHERE product_key = ? "
            "AND invalidated_at IS NULL ORDER BY id DESC LIMIT 1",
            (product_key,),
        )

    def invalidate(self, product_keys: Iterable[str], reason: str) -> int:
        """Invalidate every live snapshot for the given identities.

        Idempotent: already-invalidated rows are left untouched, and no history
        is removed.
        """
        keys = [str(key) for key in dict.fromkeys(product_keys) if key]
        if not keys:
            return 0
        marked = 0
        now = iso()
        for start in range(0, len(keys), 400):
            chunk = keys[start : start + 400]
            placeholders = ",".join("?" for _ in chunk)
            cursor = self.db.execute(
                f"UPDATE valuation_snapshots SET invalidated_at=?, invalid_reason=? "
                f"WHERE product_key IN ({placeholders}) AND invalidated_at IS NULL",
                [now, reason[:200], *chunk],
            )
            marked += max(0, cursor.rowcount or 0)
        return marked

    def invalidate_contaminated_observations(self) -> int:
        """Retire observations filed under an identity the listing no longer has.

        Raw rows are kept (they are evidence of what was observed) but they stop
        feeding valuations. Running this repeatedly is safe.
        """
        cursor = self.db.execute(
            """
            UPDATE market_observations
            SET invalidated_at = ?, invalid_reason = 'listing identity changed'
            WHERE invalidated_at IS NULL
              AND source_id LIKE 'listing:%'
              AND EXISTS (
                  SELECT 1 FROM listings l
                  WHERE ('listing:' || l.id) = market_observations.source_id
                    AND (l.product_key IS NULL
                         OR l.product_key != market_observations.product_key
                         OR COALESCE(l.listing_kind, '') != COALESCE(market_observations.kind, ''))
              )
            """,
            (iso(),),
        )
        return max(0, cursor.rowcount or 0)

    def history(self, product_key: str, limit: int = 24) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM valuation_snapshots WHERE product_key = ? "
            "ORDER BY id DESC LIMIT ?",
            (product_key, limit),
        )
        return [dict(row) for row in rows]

    def reuse_days(self) -> int:
        return max(1, self.db.setting_int("history_reuse_days", 7))

    def is_stale(self, row: dict[str, Any] | None) -> bool:
        if not row:
            return True
        refreshed = parse_iso(row.get("refreshed_at"))
        if not refreshed:
            return True
        return (utcnow() - refreshed).total_seconds() > self.reuse_days() * 86400

    def detail(self, product_key: str) -> dict[str, Any]:
        latest = self.latest(product_key)
        if not latest:
            return {"product_key": product_key, "snapshot": None, "stale": True}
        meta = {}
        try:
            meta = json.loads(latest.get("sources") or "{}")
        except (TypeError, ValueError):
            meta = {}
        return {
            "product_key": product_key,
            "snapshot": {
                "resale_good": latest["resale_good"],
                "low": latest["resale_low"],
                "high": latest["resale_high"],
                "confidence": latest["confidence"],
                "evidence_level": latest["evidence_level"],
                "sample_size": latest["sample_size"],
                "sold_count": latest["sold_count"],
                "method": latest["method"],
                "refreshed_at": latest["refreshed_at"],
                "marketplaces": meta.get("marketplaces", {}),
                "strategies": meta.get("strategies", {}),
            },
            "stale": self.is_stale(latest),
            "reuse_days": self.reuse_days(),
            "history": [
                {
                    "resale_good": row["resale_good"],
                    "confidence": row["confidence"],
                    "refreshed_at": row["refreshed_at"],
                }
                for row in self.history(product_key, 12)
            ],
        }

    # --- refresh scheduling ------------------------------------------------
    def stale_products(
        self, limit: int = 8, priority: Iterable[str] = ()
    ) -> list[dict[str, Any]]:
        """Products whose price memory needs refreshing, most useful first.

        Priority keys (live deals, ending auctions) are returned first, then the
        products with the most active listings, then the oldest snapshots.
        """
        rows = self.db.query(
            """
            SELECT s.product_key, s.kind, s.refreshed_at, s.resale_good, s.confidence
            FROM valuation_snapshots s
            JOIN (
                SELECT product_key, MAX(id) AS id FROM valuation_snapshots
                WHERE invalidated_at IS NULL
                GROUP BY product_key
            ) latest ON latest.id = s.id
            """
        )
        priority_set = list(dict.fromkeys(priority))
        counts = {
            row["product_key"]: int(row["n"])
            for row in self.db.query(
                """
                SELECT product_key, COUNT(*) AS n FROM listings
                WHERE status='active' AND product_key IS NOT NULL
                GROUP BY product_key
                """
            )
        }
        stale = []
        for row in rows:
            refreshed = parse_iso(row["refreshed_at"])
            age_hours = (
                (utcnow() - refreshed).total_seconds() / 3600 if refreshed else 10**6
            )
            if age_hours <= self.reuse_days() * 24:
                continue
            stale.append({**dict(row), "age_hours": round(age_hours, 1)})
        rank = {key: index for index, key in enumerate(priority_set)}
        stale.sort(
            key=lambda row: (
                rank.get(row["product_key"], len(rank)),
                -counts.get(row["product_key"], 0),
                -row["age_hours"],
            )
        )
        return stale[:limit]

    # --- duplicate suppression --------------------------------------------
    def claim(self, product_key: str) -> bool:
        """True when this process may research the product now."""
        row = self.db.query_one(
            "SELECT product_key, started_at, status FROM pricing_research WHERE product_key = ?",
            (product_key,),
        )
        now = utcnow()
        if row:
            started = parse_iso(row["started_at"])
            if (
                row["status"] == "running"
                and started
                and (now - started).total_seconds() < CLAIM_MINUTES * 60
            ):
                return False
        self.db.execute(
            """
            INSERT INTO pricing_research(product_key, started_at, status, finished_at, error)
            VALUES (?, ?, 'running', NULL, NULL)
            ON CONFLICT(product_key) DO UPDATE SET
                started_at=excluded.started_at, status='running',
                finished_at=NULL, error=NULL
            """,
            (product_key, iso()),
        )
        return True

    def release(self, product_key: str, status: str, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE pricing_research SET status=?, finished_at=?, error=? WHERE product_key=?",
            (status, iso(), (error or "")[:400] or None, product_key),
        )

    # --- diagnostics -------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        row = self.db.query_one(
            """
            SELECT COUNT(*) AS snapshots, COUNT(DISTINCT product_key) AS products,
                   MAX(refreshed_at) AS latest,
                   SUM(CASE WHEN invalidated_at IS NOT NULL THEN 1 ELSE 0 END) AS invalidated
            FROM valuation_snapshots
            """
        )
        pending = self.db.query_one(
            """
            SELECT COUNT(*) AS n FROM pricing_research
            WHERE status = 'running'
            """
        )
        return {
            "snapshots": int(row["snapshots"] or 0) if row else 0,
            "products": int(row["products"] or 0) if row else 0,
            "latest_snapshot_at": row["latest"] if row else None,
            "invalidated_snapshots": int(row["invalidated"] or 0) if row else 0,
            "running_research": int(pending["n"] or 0) if pending else 0,
            "reuse_days": self.reuse_days(),
        }
