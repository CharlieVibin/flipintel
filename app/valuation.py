"""Resale valuation and deal scoring.

The engine deliberately avoids black-box guessing. It builds a comparable
market from observed listings and (when available) sold prices / CeX quotes,
normalises them for condition and negotiation, then subtracts real selling
costs. Confidence reflects sample size, dispersion, identification quality and
the strength of the price evidence. Ask prices are never labelled as sold.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .db import Database
from .identification import CONDITION_FACTORS
from .risk import RiskAssessment, assess_listing
from .utils import (
    age_hours,
    clamp,
    hours_ago,
    iso,
    json_dumps,
    median,
    quantile,
    utcnow,
    weighted_median,
    weighted_quantile,
)

MIN_COMPS = 3
SUPPORTED_KINDS = {"gpu", "cpu", "ram", "ssd", "motherboard", "pc", "laptop", "console", "monitor", "phone", "tablet", "networking"}


@dataclass
class ValuationConfig:
    min_profit: float = 20.0
    min_roi: float = 0.12
    min_confidence: float = 0.42
    ebay_fvf: float = 0.1322
    ebay_fixed_fee: float = 0.30
    resale_channel: str = "ebay"
    resale_shipping: float = 4.50
    packaging_cost: float = 1.00
    collection_cost: float = 0.0
    other_costs: float = 0.0
    vinted_buyer_fee_pct: float = 0.05
    vinted_buyer_fee_fixed: float = 0.70
    vinted_shipping: float = 3.49
    asking_to_sold_factor: float = 0.85
    cex_private_factor: float = 0.80
    category_factors: dict[str, float] = field(default_factory=dict)
    calibration_active: bool = False
    calibration_note: str = ""

    @classmethod
    def from_db(cls, db: Database) -> "ValuationConfig":
        values = db.get_settings()
        def f(key: str, default: float) -> float:
            try:
                return float(values.get(key, default))
            except (TypeError, ValueError):
                return default
        def s(key: str, default: str) -> str:
            return str(values.get(key, default)).strip().lower()
        channel = s("resale_channel", "ebay")
        if channel not in {"ebay", "vinted", "facebook"}:
            channel = "ebay"
        return cls(
            min_profit=f("min_profit", 20.0),
            min_roi=f("min_roi", 0.12),
            min_confidence=f("min_confidence", 0.42),
            ebay_fvf=f("ebay_fvf", 0.1322),
            ebay_fixed_fee=f("ebay_fixed_fee", 0.30),
            resale_channel=channel,
            resale_shipping=f("resale_shipping", 4.50),
            packaging_cost=f("packaging_cost", 1.00),
            collection_cost=f("collection_cost", 0.0),
            other_costs=f("other_costs", 0.0),
            vinted_buyer_fee_pct=f("vinted_buyer_fee_pct", 0.05),
            vinted_buyer_fee_fixed=f("vinted_buyer_fee_fixed", 0.70),
            vinted_shipping=f("vinted_shipping", 3.49),
            asking_to_sold_factor=f("asking_to_sold_factor", 0.85),
            cex_private_factor=f("cex_private_factor", 0.80),
        )


@dataclass
class MarketEstimate:
    resale_good: float | None
    low: float | None
    high: float | None
    confidence: float
    sample_size: int
    method: str
    comps: list[dict[str, Any]] = field(default_factory=list)
    sold_count: int = 0
    asking_count: int = 0
    dispersion: float = 0.0
    range_supported: bool = False
    range_basis: str = ""
    notes: list[str] = field(default_factory=list)
    ci_low: float | None = None
    ci_high: float | None = None
    trend_pct: float | None = None
    trend_note: str = ""
    market_values: list[float] = field(default_factory=list)


def _log_stats(values: list[float]) -> tuple[float, float]:
    logs = [math.log(v) for v in values if v > 0]
    if not logs:
        return 0.0, 0.0
    med = median(logs) or 0.0
    deviations = [abs(v - med) for v in logs]
    mad = median(deviations) or 0.0
    return med, mad * 1.4826  # robust sigma


def _clean_and_weight(
    rows: list[dict[str, Any]], config: ValuationConfig, kind: str = "other"
) -> list[dict[str, Any]]:
    """Condition-normalise rows and attach evidence weights.

    Sold evidence is deliberately kept separate from asking prices. A manual
    sold comparable is the strongest evidence, followed by an automatically
    observed completed sale. Asking prices are a fallback only.
    """
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        price = float(row.get("price") or 0)
        if price <= 0:
            continue
        condition = row.get("condition_norm") or "unknown"
        # A faulty/parts item is not a comparable for working hardware. The old
        # 0.30 condition factor divided its price by 0.30, turning a £120 faulty
        # monitor into a fake £400 "good" comparable.
        if str(condition).lower() == "faulty":
            continue
        factor = CONDITION_FACTORS.get(condition, 0.95)
        adjusted = price / factor
        is_sold = bool(row.get("is_sold"))
        source = str(row.get("source") or row.get("marketplace") or "").lower()
        evidence_conf = max(0.2, min(1.0, float(row.get("evidence_confidence") or 1.0)))
        age = age_hours(row.get("observed_at") or row.get("last_seen"))
        recency = 0.65 + 0.35 * math.exp(-(age or 0) / (24 * 21))
        if source == "cex":
            adjusted = price * config.cex_private_factor
            weight = 1.0
        elif is_sold:
            adjusted = price
            base_weight = 2.5 if source == "manual" else 1.9 if source == "manual_csv" else 2.2
            weight = base_weight * evidence_conf
            weight *= 0.88 + 0.12 * math.exp(-(age or 0) / (24 * 60))
        else:
            asking_factor = config.category_factors.get(kind, config.asking_to_sold_factor)
            adjusted *= asking_factor
            weight = 0.62 if row.get("marketplace") != row.get("candidate_marketplace") else 0.55
            weight *= recency
        cleaned.append(
            {
                **row,
                "source": source,
                "evidence_confidence": evidence_conf,
                "adjusted_price": round(adjusted, 2),
                "weight": max(0.05, weight),
                "is_sold": is_sold,
            }
        )
    return cleaned


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prevent the same comparable being counted twice.

    A listing that changed price is still the same market observation for
    weighting purposes; the first row wins because current listings and the
    newest snapshots are ordered first. Two genuinely distinct sales with the
    same source ID cannot exist (the evidence table enforces uniqueness).
    """
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        source = str(row.get("source") or row.get("marketplace") or "")
        source_id = str(row.get("source_id") or row.get("id") or "")
        title = " ".join(str(row.get("title") or "").lower().split())[:90]
        key = (source, source_id or title)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _trim_pool(
    pool: list[dict[str, Any]], *, asking_only: bool = False
) -> list[dict[str, Any]]:
    """Remove implausible prices using a robust log-scale MAD, not a plain
    mean, so one £5,000 outlier cannot drag the whole comparable set.

    Asking-price pools get a second, lower-tail guard. Distressed, faulty,
    accessory and scam listings cluster far below the normal asking market; if
    they remain in the pool they lower the estimated resale, which paradoxically
    makes another suspiciously cheap listing look normal. They are not valid
    evidence of what a working item sells for.
    """
    if len(pool) < MIN_COMPS:
        return pool
    values = [float(c["adjusted_price"]) for c in pool]
    logs = [math.log(value) for value in values if value > 0]
    if not logs:
        return pool
    centre = median(logs) or 0.0
    deviations = [abs(value - centre) for value in logs]
    mad = median(deviations) or 0.0
    sigma = mad * 1.4826
    if sigma <= 0.02:
        kept = pool
    else:
        low = max(5.0, math.exp(centre - 3.5 * sigma))
        high = min(25_000.0, math.exp(centre + 3.5 * sigma))
        kept = [
            c for c in pool
            if low <= float(c["adjusted_price"]) <= high
        ]
    kept = kept if len(kept) >= MIN_COMPS else pool
    if asking_only and len(kept) >= 5:
        ordered = sorted(float(c["adjusted_price"]) for c in kept)
        q1 = quantile(ordered, 0.25) or ordered[0]
        q3 = quantile(ordered, 0.75) or ordered[-1]
        # Standard Tukey lower fence. Unlike a fixed fraction of the median,
        # this survives a genuine downward market trend (the whole recent pool
        # moves together) while still rejecting isolated desperate/scam asks.
        floor = max(1.0, q1 - 1.5 * (q3 - q1))
        without_desperate = [
            c for c in kept if float(c["adjusted_price"]) >= floor
        ]
        if len(without_desperate) >= MIN_COMPS:
            kept = without_desperate
    return kept


def _estimate_from_rows(
    rows: list[dict[str, Any]],
    config: ValuationConfig,
    ident_confidence: float,
    candidate_condition: str,
    candidate_flags: list[str] | None = None,
    kind: str = "other",
) -> MarketEstimate:
    cleaned = _dedupe_rows(_clean_and_weight(rows, config, kind))
    if not cleaned:
        return MarketEstimate(
            None, None, None, 0.0, 0, "insufficient_data",
            notes=["No comparable listings or sold evidence have been recorded yet."],
        )

    sold_rows = [c for c in cleaned if c["is_sold"]]
    asking_rows = [c for c in cleaned if not c["is_sold"] and c.get("source") != "cex"]
    cex_rows = [c for c in cleaned if c.get("source") == "cex"]
    sold_pool = _trim_pool(sold_rows)
    asking_pool = _trim_pool(asking_rows, asking_only=True)
    cex_pool = _trim_pool(cex_rows)
    sold_count = len(sold_pool)
    has_cex = bool(cex_pool)
    method = "insufficient_data"
    basis: list[dict[str, Any]] = []
    evidence_n = 0
    notes: list[str] = []

    if sold_count >= 2:
        basis = sold_pool
        resale_good = weighted_median(
            (c["adjusted_price"], c["weight"]) for c in basis
        ) or 0.0
        method = "sold_comps"
        evidence_n = sold_count + 0.35 * len(asking_pool)
        notes.append(
            f"{sold_count} recent completed sale(s) used as the primary evidence; "
            "asking prices are shown for context only."
        )
    elif sold_count == 1:
        sold_value = weighted_median(
            (c["adjusted_price"], c["weight"]) for c in sold_pool
        ) or 0.0
        asking_anchor = weighted_quantile(
            ((c["adjusted_price"], c["weight"]) for c in asking_pool), 0.38
        ) if asking_pool else None
        if asking_anchor:
            resale_good = sold_value * 0.75 + asking_anchor * 0.25
            notes.append("One completed sale anchors the estimate, blended with live asking prices.")
        else:
            resale_good = sold_value
            notes.append("One completed sale is the only price evidence; treat the estimate cautiously.")
        basis = sold_pool + asking_pool
        method = "mixed_comps"
        evidence_n = 1 + 0.6 * len(asking_pool)
    elif asking_pool:
        basis = asking_pool
        resale_good = weighted_quantile(
            ((c["adjusted_price"], c["weight"]) for c in basis), 0.38
        ) or 0.0
        method = "live_asking_comps"
        evidence_n = len(basis)
        notes.append(
            "No completed-sale evidence is available; this is a conservative lower-quartile "
            "of live UK asking prices, adjusted for negotiation."
        )
        if has_cex:
            notes.append("CeX is available as a secondary retail anchor.")
    elif cex_pool:
        basis = cex_pool
        resale_good = weighted_median(
            (c["adjusted_price"], c["weight"]) for c in basis
        ) or 0.0
        method = "cex_anchor"
        evidence_n = len(basis)
        notes.append("CeX retail is the only anchor; it is adjusted for private resale.")
    else:
        return MarketEstimate(
            None, None, None, 0.0, 0, "insufficient_data",
            notes=["Comparable evidence exists, but none is usable after filtering."],
        )

    kept = sold_pool + asking_pool + cex_pool
    if not kept:
        return MarketEstimate(None, None, None, 0.0, 0, "insufficient_data")

    values = [float(c["adjusted_price"]) for c in basis if float(c["adjusted_price"]) > 0]
    if not values or resale_good <= 0:
        return MarketEstimate(None, None, None, 0.0, 0, "insufficient_data")
    log_med, sigma = _log_stats(values)
    p25 = quantile(values, 0.25) or resale_good * 0.85
    p75 = quantile(values, 0.75) or resale_good * 1.15
    p10 = quantile(values, 0.10) or p25 * 0.9
    p90 = quantile(values, 0.90) or p75 * 1.1
    iqr_ratio = p75 / p25 if p25 > 0 else 99.0

    # Time-weighted direction of the asking market. This is reported even when
    # the sample is too small to adjust the estimate, so the dashboard can be
    # honest about the evidence.
    recent = [
        c for c in kept
        if (age_hours(c.get("observed_at") or c.get("last_seen")) or 9999) <= 24 * 14
    ]
    older = [
        c for c in kept
        if 24 * 15 <= (age_hours(c.get("observed_at") or c.get("last_seen")) or 9999) <= 24 * 60
    ]
    trend_pct: float | None = None
    trend_note = ""
    if len(recent) >= 4 and len(older) >= 4:
        recent_median = median([float(c["adjusted_price"]) for c in recent]) or 0.0
        older_median = median([float(c["adjusted_price"]) for c in older]) or 0.0
        if older_median > 0:
            trend_pct = (recent_median - older_median) / older_median
            if abs(trend_pct) >= 0.05:
                trend_note = (
                    f"Asking prices are {abs(trend_pct):.0%} "
                    f"{'lower' if trend_pct < 0 else 'higher'} than the preceding 45 days."
                )
                notes.append(trend_note)

    condition_factor = CONDITION_FACTORS.get(candidate_condition, 0.95)
    # A falling market should lower a conservative estimate, but only modestly:
    # one short-term move is not proof of a new price level. Rising markets are
    # not used to inflate an estimate.
    range_damping = 1.0
    if sold_count == 0 and trend_pct is not None and trend_pct <= -0.05:
        damping = max(0.85, 1.0 + (trend_pct * 0.35))
        range_damping = damping
        resale_good *= damping
        notes.append(
            f"The estimate was reduced by {abs(1 - damping):.0%} to reflect the recent downward asking trend."
        )
    resale = resale_good * condition_factor
    spread = max(0.08, sigma)

    ident = clamp(ident_confidence, 0.0, 1.0)
    sample_score = clamp(evidence_n / 8.0, 0.0, 1.0)
    dispersion_score = 1.0 / (1.0 + 2.2 * spread)
    if sold_count >= 3:
        source_score = 1.0
    elif sold_count == 2:
        source_score = 0.96
    elif sold_count == 1:
        source_score = 0.86
    elif has_cex:
        source_score = 0.74
    else:
        source_score = 0.34
    freshness_values = []
    for comp in kept:
        age = age_hours(comp.get("observed_at") or comp.get("last_seen"))
        freshness_values.append(math.exp(-(age or 0) / (24 * 14)))
    freshness = sum(freshness_values) / len(freshness_values) if freshness_values else 0.0

    confidence = (
        0.26 * ident
        + 0.18 * sample_score
        + 0.18 * dispersion_score
        + 0.28 * source_score
        + 0.10 * freshness
    )
    user_only_sold = sold_count > 0 and all(
        comp.get("source") in {"manual", "manual_csv"} for comp in sold_pool
    )
    if user_only_sold:
        confidence = min(confidence, 0.82)
        notes.append(
            "All completed-sale evidence is user-supplied; it is not independently confirmed."
        )
    if sold_count == 0:
        confidence = min(confidence, 0.56 if has_cex else 0.52)
    elif sold_count == 1:
        confidence = min(confidence, 0.74)
    elif sold_count == 2:
        confidence = min(confidence, 0.84)
    if len(kept) < 5:
        confidence = min(confidence, 0.62)
        notes.append("Small evidence set — treat this estimate cautiously.")
    if len(kept) < MIN_COMPS:
        confidence = min(confidence, 0.28)
        notes.append("Fewer than three usable comparables: insufficient evidence for a confident estimate.")
    if iqr_ratio > 2.5:
        confidence *= 0.72
        notes.append("Comparable prices are widely dispersed; the lower quartile is used to avoid inflation.")
    if iqr_ratio > 4.0:
        confidence = min(confidence, 0.34)
        notes.append("Extreme price dispersion — verify the exact model and condition before buying.")
    if candidate_flags and "condition_caution" in candidate_flags:
        confidence *= 0.92
    if candidate_flags and "laptop_gpu" in candidate_flags:
        confidence *= 0.94
    if candidate_condition == "faulty":
        notes.append("Faulty items are excluded from buy recommendations.")
    if config.calibration_active and kind in config.category_factors:
        notes.append(
            f"Asking prices use a {config.category_factors[kind]:.0%} completed-sale calibration "
            f"for {kind} validated on held-out products."
        )

    range_supported = len(kept) >= 3 and spread <= 0.75
    range_basis = (
        "Interquartile range of condition-normalised comparable prices; not a guaranteed sale price."
        if range_supported else
        "Too few comparable observations or dispersion is too wide for a defensible range."
    )

    public_comps = []
    for comp in sorted(kept, key=lambda c: (not bool(c["is_sold"]), -float(c["weight"])))[:12]:
        evidence = "manual_sold" if comp["is_sold"] and comp.get("source") == "manual" else (
            "sold" if comp["is_sold"] else ("cex" if comp.get("source") == "cex" else "asking")
        )
        public_comps.append(
            {
                "title": comp.get("title"),
                "price": round(float(comp.get("price") or 0), 2),
                "adjusted_price": comp["adjusted_price"],
                "marketplace": comp.get("marketplace") or comp.get("source"),
                "condition": comp.get("condition_norm") or "unknown",
                "is_sold": bool(comp.get("is_sold")),
                "evidence": evidence,
                "source": comp.get("source"),
                "postage": round(float(comp.get("postage") or 0), 2),
                "notes": comp.get("notes"),
                "url": comp.get("url"),
                "observed_at": comp.get("observed_at") or comp.get("last_seen"),
            }
        )

    return MarketEstimate(
        resale_good=round(resale_good, 2),
        low=round((p25 or resale) * condition_factor * range_damping, 2),
        high=round((p75 or resale) * condition_factor * range_damping, 2),
        confidence=round(clamp(confidence, 0.0, 0.98), 3),
        sample_size=len(kept),
        method=method,
        comps=public_comps,
        sold_count=sold_count,
        asking_count=len(asking_pool),
        dispersion=round(spread, 3),
        range_supported=range_supported,
        range_basis=range_basis,
        notes=notes,
        ci_low=round((p10 or p25) * condition_factor * range_damping, 2),
        ci_high=round((p90 or p75) * condition_factor * range_damping, 2),
        trend_pct=round(trend_pct, 4) if trend_pct is not None else None,
        trend_note=trend_note,
        market_values=[round(value, 2) for value in values],
    )


class ValuationEngine:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._comp_cache: dict[tuple[str, str], list[dict[str, Any]]] | None = None

    # --- data gathering -----------------------------------------------------
    def _market_comps(self, product_key: str, kind: str, exclude_id: int) -> list[dict[str, Any]]:
        cache_key = (product_key, kind)
        if self._comp_cache is not None and cache_key in self._comp_cache:
            return self._comp_cache[cache_key]
        excluded = exclude_id if self._comp_cache is None else 0
        rows = self.db.query(
            """
            SELECT id, title, price, condition_norm, marketplace, url, last_seen AS observed_at,
                   is_sold, source, candidate_marketplace, postage, notes, verified,
                   evidence_confidence, source_id
            FROM (
                SELECT l.id, l.title, l.price, l.condition_norm, l.marketplace, l.url,
                       l.last_seen, 0 AS is_sold, l.marketplace AS source,
                       l.marketplace AS candidate_marketplace, 0 AS postage, NULL AS notes,
                       0 AS verified, 1.0 AS evidence_confidence, 'listing:' || l.id AS source_id
                FROM listings l
                WHERE l.product_key = ? AND l.listing_kind = ? AND l.status = 'active'
                  AND l.last_seen >= ? AND l.id != ? AND l.is_faulty = 0 AND l.is_wanted = 0
                  AND COALESCE(l.condition_norm, 'unknown') != 'faulty'
                  AND l.is_bundle = 0 AND l.price >= 5 AND l.price <= 25000
                ORDER BY l.last_seen DESC LIMIT 250
            )
            """,
            (product_key, kind, hours_ago(24 * 30), excluded),
        )
        observations = self.db.query(
            """
            SELECT 0 AS id, o.title, o.price, o.condition_norm, o.marketplace, NULL AS url,
                   0 AS is_sold, o.marketplace AS source, '' AS candidate_marketplace,
                   0 AS postage, NULL AS notes, 0 AS verified,
                   1.0 AS evidence_confidence, o.source_id, o.observed_at
            FROM market_observations o
            LEFT JOIN listings l ON l.marketplace=o.marketplace
                AND ('listing:' || l.id)=o.source_id
            WHERE o.product_key = ? AND (o.kind = ? OR o.kind IS NULL)
              AND o.observed_at >= ?
              AND COALESCE(l.is_bundle, 0)=0 AND COALESCE(l.is_faulty, 0)=0
              AND COALESCE(l.is_wanted, 0)=0
              AND COALESCE(l.condition_norm, 'unknown') != 'faulty'
              AND (l.id IS NULL OR o.product_key = l.product_key)
            ORDER BY o.observed_at DESC LIMIT 300
            """,
            (product_key, kind, hours_ago(24 * 90)),
        )
        external = self.db.query(
            """
            SELECT id, title, price, condition_norm, marketplace, url, observed_at,
                   is_sold, source, '' AS candidate_marketplace, postage, notes, verified,
                   evidence_confidence, source_id
            FROM market_comps
            WHERE product_key = ? AND (kind = ? OR kind IS NULL) AND observed_at >= ?
            ORDER BY is_sold DESC, observed_at DESC LIMIT 120
            """,
            (product_key, kind, hours_ago(24 * 180)),
        )
        combined = rows + observations + external
        if self._comp_cache is not None:
            self._comp_cache[cache_key] = combined
        return combined

    def _gpu_fallback_comps(self, gpu_key: str) -> list[dict[str, Any]]:
        return self.db.query(
            """
            SELECT l.id, l.title, l.price, l.condition_norm, l.marketplace, l.url,
                   l.last_seen AS observed_at, 0 AS is_sold, l.marketplace AS source,
                   l.marketplace AS candidate_marketplace, 0 AS postage, NULL AS notes,
                   0 AS verified, 1.0 AS evidence_confidence, 'listing:' || l.id AS source_id
            FROM listings l
            WHERE l.product_key = ? AND l.listing_kind = 'gpu' AND l.status = 'active'
              AND l.last_seen >= ? AND l.is_faulty = 0 AND l.is_wanted = 0 AND l.is_bundle = 0
              AND l.price >= 20 AND l.price <= 25000
            ORDER BY l.last_seen DESC LIMIT 120
            """,
            (gpu_key, hours_ago(24 * 30)),
        )

    def _cex_comp(self, product_key: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM external_quotes WHERE product_key = ? AND source = 'cex' AND status = 'ok'",
            (product_key,),
        )
        if not row or not row.get("sell_price"):
            return None
        return {
            "title": row.get("title") or "CeX quote",
            "price": float(row["sell_price"]),
            "condition_norm": "good",
            "marketplace": "cex",
            "url": row.get("url"),
            "observed_at": row.get("fetched_at"),
            "is_sold": 0,
            "source": "cex",
            "candidate_marketplace": "",
            "postage": 0,
            "notes": "CeX retail anchor",
            "verified": 0,
            "evidence_confidence": 1.0,
            "source_id": f"cex:{product_key}",
        }

    # --- per-listing evaluation --------------------------------------------
    def evaluate(self, listing: dict[str, Any], config: ValuationConfig) -> dict[str, Any]:
        listing_id = int(listing["id"])
        kind = listing.get("listing_kind") or "other"
        product_key = listing.get("product_key")
        price = float(listing.get("price") or 0)
        marketplace = listing.get("marketplace") or ""
        flags = listing.get("flags_list") or []
        method = "insufficient_data"
        notes: list[str] = []
        estimate: MarketEstimate | None = None

        unvaluable = (
            not product_key
            or kind not in SUPPORTED_KINDS
            or product_key in {"pc:generic", "laptop:generic"}
        )
        if unvaluable:
            notes.append("Model could not be identified confidently enough for a resale estimate.")
        else:
            rows = self._market_comps(product_key, kind, listing_id)
            rows = [
                row for row in rows
                if str(row.get("source_id") or "") != f"listing:{listing_id}"
            ]
            cex = self._cex_comp(product_key)
            if cex:
                rows.append(cex)
            estimate = _estimate_from_rows(
                rows,
                config,
                float(listing.get("identification_confidence") or 0),
                listing.get("condition_norm") or "unknown",
                flags,
                kind,
            )
            method = estimate.method
            notes = estimate.notes
            if kind == "laptop":
                # Laptop comparables depend heavily on exact spec, so cap confidence.
                estimate.confidence = min(estimate.confidence, 0.66)
            elif kind == "pc":
                estimate.confidence = min(estimate.confidence, 0.72)
            elif kind in {"ram", "ssd", "monitor"}:
                estimate.confidence = min(estimate.confidence, 0.68)

            if kind == "pc" and (estimate.sample_size < MIN_COMPS) and listing.get("attrs_json"):
                import json
                attrs = json.loads(listing["attrs_json"]) if listing.get("attrs_json") else {}
                gpu_key = attrs.get("gpu_key")
                if gpu_key:
                    gpu_rows = self._gpu_fallback_comps(gpu_key)
                    if gpu_rows:
                        gpu_est = _estimate_from_rows(
                            gpu_rows, config, float(listing.get("identification_confidence") or 0) * 0.85,
                            "good", flags, "gpu",
                        )
                        if gpu_est.resale_good:
                            resale = gpu_est.resale_good * 0.85 + 170.0
                            condition_factor = CONDITION_FACTORS.get(listing.get("condition_norm") or "unknown", 0.95)
                            resale *= condition_factor
                            estimate = MarketEstimate(
                                resale_good=round(resale, 2),
                                low=round(resale * 0.78, 2),
                                high=round(resale * 1.22, 2),
                                confidence=round(min(0.52, gpu_est.confidence * 0.72), 3),
                                sample_size=gpu_est.sample_size,
                                method="component_model",
                                comps=gpu_est.comps,
                                sold_count=gpu_est.sold_count,
                                asking_count=gpu_est.asking_count,
                                notes=[
                                    "Value modelled from the installed GPU plus estimated cost of "
                                    "the remaining system (CPU, board, RAM, storage, PSU, case).",
                                    "Complete-PC comparables were too few; verify the full spec before buying.",
                                ],
                            )
                            method = "component_model"
                            notes = estimate.notes

        purchase_shipping = 0.0
        buyer_fee = 0.0
        if marketplace == "vinted":
            purchase_shipping = config.vinted_shipping
            buyer_fee = round(price * config.vinted_buyer_fee_pct + config.vinted_buyer_fee_fixed, 2)
        elif marketplace == "ebay":
            purchase_shipping = float(listing.get("shipping") or 0)
        purchase_total = round(price + purchase_shipping + buyer_fee, 2)

        if estimate and estimate.resale_good and "contents_incomplete" in flags and kind in {
            "console", "phone", "tablet", "laptop"
        }:
            # Missing controllers, charger, cables or dock genuinely reduce what
            # a buyer will pay. Apply a fixed contents discount to the central
            # estimate and the supported range rather than valuing the item as
            # a complete bundle.
            contents_factor = 0.88
            for attribute in ("resale_good", "low", "high"):
                value = getattr(estimate, attribute)
                if value:
                    setattr(estimate, attribute, round(value * contents_factor, 2))
            notes.append(
                "Resale estimate reduced by 12% because the listing states that controllers, "
                "cables, charger or other included accessories are missing."
            )
        expected_resale = estimate.resale_good if estimate else None
        # A price that is less than 30% of the modelled resale is far outside
        # normal negotiation. Treat it as a manual-review signal rather than
        # an automatic recommendation: these are often parts, empty boxes or
        # misleading titles.
        suspicious_price = bool(
            expected_resale
            and expected_resale > 0
            and (
                purchase_total < expected_resale * 0.30
                # Memory and storage are easy to misread (capacity, generation,
                # modules), so demand more evidence before calling a deep
                # discount a genuine deal.
                or (kind in {"ram", "ssd"} and purchase_total < expected_resale * 0.40)
                # A 60%+ discount on a high-value item is unusual enough to
                # deserve a human look before it is called a profit.
                or (expected_resale >= 200 and purchase_total < expected_resale * 0.40)
            )
        )
        risk = assess_listing(
            listing,
            estimate,
            expected_resale=expected_resale,
            purchase_total=purchase_total,
        )
        # resale_good already has candidate condition applied in the estimate
        resale_fees = 0.0
        net_profit = None
        net_profit_conservative = None
        net_profit_optimistic = None
        roi = None
        score = 0.0
        is_deal = False
        review_decision = str(listing.get("review_decision") or "")
        review_resolved = review_decision in {"approved", "corrected"}
        needs_review = (suspicious_price or (
            expected_resale is not None and risk.review_recommended
        )) and not review_resolved
        review_reasons: list[str] = []
        if suspicious_price:
            review_reasons.append(
                "Price is unusually far below the modelled resale value; verify contents, model and condition"
            )
        if risk.review_recommended and risk.reasons:
            review_reasons.extend(risk.reasons[:4])
        if needs_review:
            if "bundle" in flags:
                review_reasons.append("Bundle listing; do not assume every mentioned item is included")
            if "accessory_or_box" in flags or "compatibility_accessory" in flags:
                review_reasons.append("May be an accessory, compatibility item or packaging rather than the device")
            if "model_unclear" in flags:
                review_reasons.append("Exact model is ambiguous")
            if "condition_caution" in flags:
                review_reasons.append("Description includes condition caveats")
            if "faulty_or_parts" in flags or "parts_only" in flags:
                review_reasons.append("Faulty, spares or parts-only listing")
            if "variant_unclear" in flags:
                review_reasons.append(
                    "Storage, memory or chip variant is not stated; do not assume the comparable "
                    "listings have the same specification"
                )
        review_reasons = list(dict.fromkeys(review_reasons))
        review_reason = "; ".join(review_reasons) if review_reasons else None
        channel = config.resale_channel if config.resale_channel in {"ebay", "vinted", "facebook"} else "ebay"
        channel_label = {
            "ebay": "eBay UK (private seller)",
            "vinted": "Vinted (seller pays no selling fee)",
            "facebook": "Facebook Marketplace (local collection)",
        }[channel]
        reasons: list[str] = []

        if expected_resale and expected_resale > 0:
            if channel == "ebay":
                resale_fees = round(expected_resale * config.ebay_fvf + config.ebay_fixed_fee, 2)
            else:
                resale_fees = 0.0
            def net_at(resale_value: float) -> float:
                fees = (
                    resale_value * config.ebay_fvf + config.ebay_fixed_fee
                    if channel == "ebay" else 0.0
                )
                proceeds = (
                    resale_value - fees - config.resale_shipping - config.packaging_cost
                    - config.collection_cost - config.other_costs
                )
                return round(proceeds - purchase_total, 2)

            net_profit = net_at(expected_resale)
            if estimate and estimate.range_supported and estimate.low is not None:
                net_profit_conservative = net_at(estimate.low)
            if estimate and estimate.range_supported and estimate.high is not None:
                net_profit_optimistic = net_at(estimate.high)
            roi = round(net_profit / purchase_total, 4) if purchase_total > 0 else None
            gap = (expected_resale - purchase_total) / expected_resale
            age = age_hours(listing.get("first_seen")) or 0
            profit_component = clamp(net_profit / max(45.0, expected_resale * 0.35), 0, 1) * 30
            roi_component = clamp((roi or 0) / 0.55, 0, 1) * 30
            gap_component = clamp(gap / 0.40, 0, 1) * 15
            freshness_component = math.exp(-age / 72.0) * 10
            base_score = profit_component + roi_component + gap_component + freshness_component
            confidence = estimate.confidence if estimate else 0.0
            score = round(clamp(base_score * (0.55 + 0.45 * confidence), 0, 100), 1)

            if suspicious_price:
                # The review penalty stays after approval: approving a listing
                # confirms the user accepts the risk, it does not make the
                # price evidence more reliable.
                estimate.confidence = min(0.55, round(estimate.confidence * 0.88, 3))
                confidence = estimate.confidence
                if not review_resolved:
                    reasons.append(
                        "Flagged for manual review: price is unusually far below the modelled resale value"
                    )
            if review_resolved:
                reasons.append(
                    "Approved after manual review; the automatic risk signals remain visible below"
                )
            if risk.score >= 30:
                estimate.confidence = min(
                    estimate.confidence,
                    0.40 if risk.level == "high" else 0.58,
                )
                confidence = estimate.confidence
                reasons.append(
                    f"Risk assessment {risk.score:.0f}/100 ({risk.level}); see the review reasons before buying"
                )
            is_deal = (
                net_profit >= config.min_profit
                and (roi or 0) >= config.min_roi
                and confidence >= config.min_confidence
                and not listing.get("is_faulty")
                and not listing.get("is_wanted")
                and expected_resale > purchase_total
                and (estimate.sample_size if estimate else 0) >= 4
                and float(listing.get("identification_confidence") or 0) >= 0.55
                and not listing.get("is_bundle")
                and not needs_review
                and (
                    estimate.sold_count >= 1
                    or (estimate.confidence >= 0.48 and estimate.sample_size >= 5)
                    # A human approval resolves the risk gate, not the data
                    # confidence. It still needs the configured minimum
                    # confidence and a real comparable sample.
                    or (
                        review_resolved
                        and estimate.confidence >= config.min_confidence
                        and estimate.sample_size >= 4
                    )
                )
            )
            if estimate and estimate.sample_size < 4:
                reasons.append("Fewer than 4 comparables — not enough evidence for a recommendation")
            if net_profit > 0:
                reasons.append(f"£{net_profit:,.0f} estimated net profit after fees and delivery")
            reasons.append(f"Priced {gap:.0%} below modelled resale")
            if estimate:
                reasons.append(
                    f"{estimate.sample_size} comparable UK listing(s)"
                    + (f", {estimate.sold_count} completed sale(s)" if estimate.sold_count else ", live asking prices")
                )
                if estimate.sold_count == 0:
                    reasons.append(
                        "No completed-sale evidence — this is an asking-price estimate, not a verified resale value"
                    )
                if estimate.confidence < 0.5:
                    reasons.append("Lower-confidence valuation — check the exact model and condition")
            if listing.get("is_faulty"):
                reasons.append("Faulty or spares listing — not recommended")
            if listing.get("is_wanted"):
                reasons.append("Wanted advertisement, not a sale")
            if listing.get("price_dropped_at"):
                reasons.append("Price has recently been reduced")
            if listing.get("is_bundle"):
                reasons.append("Bundle listing — not valued as a single item")
            if (roi or 0) < config.min_roi:
                reasons.append(f"ROI below the {config.min_roi:.0%} threshold")
            if net_profit < config.min_profit:
                reasons.append(f"Profit below the £{config.min_profit:.0f} threshold")
            if confidence < config.min_confidence:
                reasons.append(f"Confidence below the {config.min_confidence:.0%} threshold")

        breakdown = {
            "purchase": {
                "price": round(price, 2),
                "shipping": round(purchase_shipping, 2),
                "buyer_fee": round(buyer_fee, 2),
                "total": purchase_total,
            },
            "resale": {
                "channel": channel_label,
                "expected": expected_resale,
                "low": estimate.low if estimate and estimate.range_supported else None,
                "high": estimate.high if estimate and estimate.range_supported else None,
                "range_supported": estimate.range_supported if estimate else False,
                "range_basis": estimate.range_basis if estimate else "No estimate available.",
                "conservative": estimate.low if estimate and estimate.range_supported else None,
                "central": expected_resale,
                "optimistic": estimate.high if estimate and estimate.range_supported else None,
                "fees": resale_fees,
                "shipping": config.resale_shipping,
                "packaging": config.packaging_cost,
                "collection": config.collection_cost,
                "other": config.other_costs,
            },
            "comps": estimate.comps if estimate else [],
            "sample_size": estimate.sample_size if estimate else 0,
            "sold_count": estimate.sold_count if estimate else 0,
            "asking_count": estimate.asking_count if estimate else 0,
            "dispersion": estimate.dispersion if estimate else None,
            "valuation_notes": notes,
            "assumptions": {
                "asking_to_sold_factor": config.category_factors.get(kind, config.asking_to_sold_factor),
                "base_asking_to_sold_factor": config.asking_to_sold_factor,
                "calibration_active": config.calibration_active,
                "calibration_note": config.calibration_note,
                "resale_channel": channel,
                "ebay_fvf": config.ebay_fvf,
                "ebay_fixed_fee": config.ebay_fixed_fee,
                "other_costs": config.other_costs,
                "cex_private_factor": config.cex_private_factor,
            },
            "risk": risk.as_dict(),
            "evidence": {
                "sold_evidence": bool(estimate and estimate.sold_count),
                "sold_count": estimate.sold_count if estimate else 0,
                "asking_count": estimate.asking_count if estimate else 0,
                "method": method,
                "confidence": estimate.confidence if estimate else 0.0,
                "confidence_low": estimate.ci_low if estimate else None,
                "confidence_high": estimate.ci_high if estimate else None,
                "trend_pct": estimate.trend_pct if estimate else None,
                "trend_note": estimate.trend_note if estimate else "",
            },
        }

        return {
            "listing_id": listing_id,
            "product_key": product_key,
            "kind": kind,
            "expected_resale": round(expected_resale, 2) if expected_resale else None,
            "resale_low": estimate.low if estimate and estimate.range_supported else None,
            "resale_high": estimate.high if estimate and estimate.range_supported else None,
            "confidence": estimate.confidence if estimate else 0.0,
            "sample_size": estimate.sample_size if estimate else 0,
            "valuation_method": method,
            "purchase_total": purchase_total,
            "resale_fees": resale_fees,
            "resale_shipping": config.resale_shipping if expected_resale else 0.0,
            "packaging": config.packaging_cost if expected_resale else 0.0,
            "net_profit": net_profit,
            "net_profit_conservative": net_profit_conservative,
            "net_profit_optimistic": net_profit_optimistic,
            "roi": roi,
            "score": score,
            "is_deal": int(is_deal),
            "needs_review": int(needs_review),
            "review_reason": review_reason,
            "risk_score": round(risk.score, 1),
            "risk_level": risk.level,
            "risk_reasons": risk.reasons,
            "reasons": reasons,
            "breakdown": breakdown,
            "computed_at": iso(),
        }

    # --- refresh ------------------------------------------------------------
    def _record_market_observations(self) -> int:
        """Persist one daily asking-price snapshot per tracked listing.

        This preserves market evidence when a listing disappears, and it lets
        the valuation report a time-based trend. It is an asking-price record,
        never a completed-sale claim.
        """
        rows = self.db.query(
            """
            SELECT id, product_key, listing_kind, marketplace, title, price, condition_norm
            FROM listings
            WHERE status='active' AND product_key IS NOT NULL AND last_seen >= ?
              AND is_bundle=0 AND is_faulty=0 AND is_wanted=0
            """,
            (hours_ago(24 * 7),),
        )
        if not rows:
            return 0
        now = iso()
        day = utcnow().date().isoformat()
        values = [
            (
                row["product_key"], row["listing_kind"], row["marketplace"],
                f"listing:{row['id']}", row["title"], float(row["price"]),
                row["condition_norm"], now, day, now,
            )
            for row in rows
            if float(row["price"] or 0) > 0
        ]
        if not values:
            return 0
        self.db.executemany(
            """
            INSERT INTO market_observations(
                product_key, kind, marketplace, source_id, title, price,
                condition_norm, observed_at, observed_day, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(marketplace, source_id, observed_day) DO UPDATE SET
                product_key=excluded.product_key, kind=excluded.kind,
                title=excluded.title, price=excluded.price,
                condition_norm=excluded.condition_norm, observed_at=excluded.observed_at
            """,
            values,
        )
        return len(values)

    def refresh(self, limit: int = 100000) -> dict[str, int]:
        from .calibration import build_calibration

        config = ValuationConfig.from_db(self.db)
        calibration = build_calibration(self.db)
        config.category_factors = dict(calibration.category_factors)
        config.calibration_active = calibration.active
        config.calibration_note = calibration.reason
        observed = self._record_market_observations()
        rows = self.db.query(
            """
            SELECT * FROM listings
            WHERE status = 'active' AND last_seen >= ?
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (hours_ago(24 * 21), limit),
        )
        import json
        decisions = {
            int(row["listing_id"]): row["decision"]
            for row in self.db.query("SELECT listing_id, decision FROM review_decisions")
        }
        evaluated = deals = review = 0
        self._comp_cache = {}
        for listing in rows:
            listing["flags_list"] = json.loads(listing.get("flags") or "[]") if listing.get("flags") else []
            listing["attrs_json"] = listing.get("attrs")
            listing["review_decision"] = decisions.get(int(listing["id"]), "")
            result = self.evaluate(listing, config)
            self.db.execute(
                """
                INSERT INTO deals(listing_id, product_key, kind, expected_resale, resale_low,
                    resale_high, confidence, sample_size, valuation_method, purchase_total,
                    resale_fees, resale_shipping, packaging, net_profit,
                    net_profit_conservative, net_profit_optimistic, roi, score, is_deal,
                    needs_review, review_reason, risk_score, risk_level, risk_reasons,
                    reasons, breakdown, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(listing_id) DO UPDATE SET
                    product_key=excluded.product_key, kind=excluded.kind,
                    expected_resale=excluded.expected_resale, resale_low=excluded.resale_low,
                    resale_high=excluded.resale_high, confidence=excluded.confidence,
                    sample_size=excluded.sample_size, valuation_method=excluded.valuation_method,
                    purchase_total=excluded.purchase_total, resale_fees=excluded.resale_fees,
                    resale_shipping=excluded.resale_shipping, packaging=excluded.packaging,
                    net_profit=excluded.net_profit,
                    net_profit_conservative=excluded.net_profit_conservative,
                    net_profit_optimistic=excluded.net_profit_optimistic,
                    roi=excluded.roi, score=excluded.score,
                    is_deal=excluded.is_deal, needs_review=excluded.needs_review,
                    review_reason=excluded.review_reason,
                    risk_score=excluded.risk_score, risk_level=excluded.risk_level,
                    risk_reasons=excluded.risk_reasons,
                    reasons=excluded.reasons,
                    breakdown=excluded.breakdown, computed_at=excluded.computed_at
                """,
                (
                    result["listing_id"], result["product_key"], result["kind"],
                    result["expected_resale"], result["resale_low"], result["resale_high"],
                    result["confidence"], result["sample_size"], result["valuation_method"],
                    result["purchase_total"], result["resale_fees"], result["resale_shipping"],
                    result["packaging"], result["net_profit"] or 0.0,
                    result["net_profit_conservative"], result["net_profit_optimistic"],
                    result["roi"] or 0.0,
                    result["score"], result["is_deal"], result["needs_review"],
                    result["review_reason"], result["risk_score"], result["risk_level"],
                    json_dumps(result["risk_reasons"]), json_dumps(result["reasons"]),
                    json_dumps(result["breakdown"]), result["computed_at"],
                ),
            )
            evaluated += 1
            deals += result["is_deal"]
            review += result["needs_review"]
        # Clear only rows that are no longer part of the evaluated universe.
        # Keeping the previous flags during the loop avoids the empty/partial
        # feed that occurred when every deal was zeroed before being rebuilt.
        self.db.execute(
            "UPDATE deals SET is_deal=0, needs_review=0, review_reason=NULL "
            "WHERE listing_id IN ("
            "  SELECT d.listing_id FROM deals d JOIN listings l ON l.id=d.listing_id "
            "  WHERE l.status != 'active' OR l.last_seen < ?"
            ")",
            (hours_ago(24 * 21),),
        )
        self._comp_cache = None
        return {"evaluated": evaluated, "deals": deals, "review": review, "observed": observed}
