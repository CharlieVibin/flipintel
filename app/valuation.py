"""Resale valuation and deal scoring.

The engine deliberately avoids black-box guessing. It builds a comparable
market from observed listings and (when available) sold prices / CeX quotes,
normalises them for condition and negotiation, then subtracts real selling
costs. Confidence reflects sample size, dispersion, identification quality and
the strength of the price evidence. Ask prices are never labelled as sold.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .comparables import split_pool
from .db import Database
from .identification import CONDITION_FACTORS, identity_level_for
from .pricing import PricingDatabase
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

# Incremental refreshes skip unchanged listings, but market drift (ages, new
# comparables outside a product key, calibration) must still be reconciled.
FULL_REFRESH_HOURS = 6

# Bump when evaluation logic changes so the next refresh cannot reuse deals
# computed by the previous rules.
VALUATION_REVISION = 3


def _fingerprint(payload: Any) -> str:
    return hashlib.sha1(json_dumps(payload).encode("utf-8")).hexdigest()


def _config_fingerprint(config: "ValuationConfig") -> str:
    return _fingerprint(
        [
            VALUATION_REVISION,
            config.min_profit, config.min_roi, config.min_confidence,
            config.ebay_fvf, config.ebay_fixed_fee, config.resale_channel,
            config.resale_shipping, config.packaging_cost, config.collection_cost,
            config.other_costs, config.vinted_buyer_fee_pct,
            config.vinted_buyer_fee_fixed, config.vinted_shipping,
            config.asking_to_sold_factor, config.cex_private_factor,
            config.cex_weight, config.resale_strategy, config.min_roi_high_value,
            config.high_value_threshold, config.price_reuse_days,
            config.calibration_active, sorted(config.category_factors.items()),
            sorted(config.outcome_factors.items()),
        ]
    )


def _listing_fingerprint(listing: dict[str, Any]) -> str:
    """Everything ``evaluate`` reads from a listing, and nothing that merely
    records when it was last seen."""
    try:
        raw = json.loads(listing.get("raw_json") or "{}")
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    seller_feedback = (
        raw.get("seller_feedback"), raw.get("seller_feedback_score"),
        raw.get("seller_registration"),
    )
    return _fingerprint(
        [
            listing.get("id"), listing.get("marketplace"), listing.get("price"),
            listing.get("shipping"), listing.get("condition_norm"),
            listing.get("product_key"), listing.get("listing_kind"),
            listing.get("identification_confidence"), listing.get("identity_level"),
            listing.get("identity_basis"), listing.get("is_faulty"),
            listing.get("is_wanted"), listing.get("is_bundle"),
            listing.get("is_auction"), listing.get("auction_end_at"),
            listing.get("current_bid"), listing.get("bid_count"),
            listing.get("min_next_bid"), bool(listing.get("image_url")),
            listing.get("flags"), listing.get("attrs"), listing.get("duplicate_of"),
            listing.get("first_seen"), listing.get("review_decision"), seller_feedback,
        ]
    )


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
    cex_weight: float = 2.0
    resale_strategy: str = "balanced"
    min_roi_high_value: float = 0.18
    high_value_threshold: float = 250.0
    price_reuse_days: int = 7
    category_factors: dict[str, float] = field(default_factory=dict)
    outcome_factors: dict[str, float] = field(default_factory=dict)
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
            cex_weight=max(0.0, f("cex_weight", 2.0)),
            resale_strategy=(
                s("resale_strategy", "balanced")
                if s("resale_strategy", "balanced") in {"quick", "balanced", "max"}
                else "balanced"
            ),
            min_roi_high_value=f("min_roi_high_value", 0.18),
            high_value_threshold=f("high_value_threshold", 250.0),
            price_reuse_days=int(f("history_reuse_days", 7)),
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
    evidence_level: str = "insufficient"
    strategies: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, int] = field(default_factory=dict)
    # How the resale figure was evidenced. "asking_only" means no completed
    # sale supports it and the UI must say so rather than imply a probability.
    confidence_basis: str = "asking_only"
    calibration_active: bool = False
    rejected: list[dict[str, Any]] = field(default_factory=list)


def _rank_opportunity(
    *,
    net_profit: float,
    roi: float | None,
    expected_resale: float,
    purchase_total: float,
    net_profit_conservative: float | None,
    evidence_level: str,
    identity_confidence: float,
    risk_score: float,
    holding_days: float,
) -> tuple[float, str, dict[str, float]]:
    """Capital-adjusted attractiveness, separate from any confidence measure.

    £30 on a £300 console is not the same opportunity as £30 on a £60 part, so
    the score weighs absolute profit, return on the capital actually tied up,
    evidence, identity, risk, resale time and the conservative downside. It is
    a ranking, never a probability of a successful sale.
    """
    profit = clamp(net_profit / max(60.0, expected_resale * 0.25), 0.0, 1.0)
    roi_score = clamp((roi or 0.0) / 0.60, 0.0, 1.0)
    capital = clamp(1.0 - purchase_total / 600.0, 0.0, 1.0)
    if net_profit_conservative is not None:
        downside = clamp(
            net_profit_conservative / max(30.0, expected_resale * 0.15), 0.0, 1.0
        )
    else:
        # Without a supported range, the downside is unknown. Score it as half
        # of the central case rather than pretending it is risk-free.
        downside = profit * 0.5
    evidence = {
        "strong": 1.0, "moderate": 0.8, "limited": 0.55, "insufficient": 0.2,
    }.get(evidence_level, 0.2)
    risk_factor = 1.0 - clamp(risk_score / 100.0, 0.0, 1.0)
    speed = clamp(1.0 - holding_days / 60.0, 0.0, 1.0)
    components = {
        "profit": round(24.0 * profit, 2),
        "roi": round(18.0 * roi_score, 2),
        "low_capital": round(8.0 * capital, 2),
        "conservative_downside": round(16.0 * downside, 2),
        "evidence": round(16.0 * evidence, 2),
        "identity": round(8.0 * clamp(identity_confidence, 0.0, 1.0), 2),
        "risk": round(6.0 * risk_factor, 2),
        "resale_speed": round(4.0 * speed, 2),
    }
    score = round(clamp(sum(components.values()), 0.0, 100.0), 1)
    if score >= 70 and evidence_level in {"moderate", "strong"}:
        tier = "strong"
    elif score >= 50:
        tier = "decent"
    elif score >= 30:
        tier = "speculative"
    else:
        tier = "weak"
    return score, tier, components


def _log_stats(values: list[float]) -> tuple[float, float]:
    logs = [math.log(v) for v in values if v > 0]
    if not logs:
        return 0.0, 0.0
    med = median(logs) or 0.0
    deviations = [abs(v - med) for v in logs]
    mad = median(deviations) or 0.0
    return med, mad * 1.4826  # robust sigma


def seller_weight(raw_json: Any) -> float:
    """Evidence weight for a marketplace seller, from data the source gave us.

    Established eBay sellers (95%+ feedback and a real history) count double a
    small seller, as proposed. Missing data never earns the higher weight, and a
    single seller is capped later so volume cannot dominate the estimate.
    """
    try:
        raw = json.loads(raw_json or "{}")
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        return 1.0
    try:
        percentage = float(str(raw.get("seller_feedback") or "").replace("%", "").strip())
    except (TypeError, ValueError):
        percentage = None
    try:
        score = int(float(raw.get("seller_feedback_score") or 0))
    except (TypeError, ValueError):
        score = 0
    if percentage is not None and percentage >= 98.5 and score >= 100:
        return 2.0
    if percentage is not None and percentage < 95:
        return 0.5
    return 1.0


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
            # Kept as a retail reference. It is converted to a private-resale
            # level later using the observed asking market when one exists, so
            # a high street retail price cannot masquerade as a sale price.
            adjusted = price * config.cex_private_factor
            weight = max(0.1, config.cex_weight)
        elif is_sold:
            adjusted = price
            base_weight = 2.5 if source == "manual" else 1.9 if source == "manual_csv" else 2.2
            weight = base_weight * evidence_conf
            weight *= 0.88 + 0.12 * math.exp(-(age or 0) / (24 * 60))
        else:
            asking_factor = config.category_factors.get(kind, config.asking_to_sold_factor)
            adjusted *= asking_factor
            weight = 0.62 if row.get("marketplace") != row.get("candidate_marketplace") else 0.55
            weight *= recency * seller_weight(row.get("raw_json"))
        cleaned.append(
            {
                **row,
                "source": source,
                "evidence_confidence": evidence_conf,
                "adjusted_price": round(adjusted, 2),
                "weight": max(0.05, weight),
                "is_sold": is_sold,
                "cex_retail": price if source == "cex" else None,
                "seller": row.get("seller") or row.get("source") or "",
            }
        )
    return cleaned


def _cap_concentration(
    rows: list[dict[str, Any]], cap: float = 0.30
) -> list[dict[str, Any]]:
    """Stop one dominant seller from setting the whole comparable market.

    A high-volume seller with twenty similar listings would otherwise outweigh
    ten independent observations. Their combined weight is scaled down to the
    cap; individual listings are still counted, just not repeatedly.
    """
    total = sum(float(row.get("weight") or 0) for row in rows)
    if total <= 0 or not rows:
        return rows
    by_seller: dict[str, float] = defaultdict(float)
    for row in rows:
        by_seller[str(row.get("seller") or "")] += float(row.get("weight") or 0)
    if len(by_seller) < 2:
        return rows
    limit = total * cap
    for row in rows:
        seller = str(row.get("seller") or "")
        weight = float(row.get("weight") or 0)
        if seller and by_seller[seller] > limit and weight > 0:
            row["weight"] = max(
                0.05, weight * (limit / by_seller[seller])
            )
    return rows


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prevent the same comparable being counted twice.

    A listing that changed price is still the same market observation for
    weighting purposes; the first row wins because current listings and the
    newest snapshots are ordered first. Two genuinely distinct sales with the
    same source ID cannot exist (the evidence table enforces uniqueness).

    Asking pools also collapse byte-identical offers (same marketplace, title,
    price and condition) posted from several accounts. That is duplicated
    inventory, not independent evidence of what the market will pay. Completed
    sales are never collapsed this way: two identical sales are two sales.
    """
    seen: set[tuple[str, str]] = set()
    seen_offers: set[tuple[str, str, float, str]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        source = str(row.get("source") or row.get("marketplace") or "")
        source_id = str(row.get("source_id") or row.get("id") or "")
        title = " ".join(str(row.get("title") or "").lower().split())[:90]
        key = (source, source_id or title)
        if key in seen:
            continue
        seen.add(key)
        if not row.get("is_sold"):
            offer = (
                source,
                title,
                round(float(row.get("price") or 0), 2),
                str(row.get("condition_norm") or "unknown"),
            )
            if offer in seen_offers:
                continue
            seen_offers.add(offer)
        unique.append(row)
    return unique


def _cap_source_weight(
    rows: list[dict[str, Any]], cap: float = 0.55
) -> list[dict[str, Any]]:
    """Stop one marketplace from defining the whole asking market.

    eBay carries far more live listings than Vinted or Gumtree, so an
    uncapped pool is effectively an eBay pool. That is a problem when eBay is
    also where business sellers list new or near-new stock. Each marketplace is
    limited to its share of the total weight, preserving the smaller private
    market's evidence instead of drowning it.
    """
    total = sum(float(row.get("weight") or 0) for row in rows)
    if total <= 0 or not rows:
        return rows
    by_source: dict[str, float] = defaultdict(float)
    for row in rows:
        by_source[str(row.get("marketplace") or row.get("source") or "")] += float(
            row.get("weight") or 0
        )
    if len(by_source) < 2:
        return rows
    limit = total * cap
    for row in rows:
        source = str(row.get("marketplace") or row.get("source") or "")
        weight = float(row.get("weight") or 0)
        if source and by_source[source] > limit and weight > 0:
            row["weight"] = max(0.05, weight * (limit / by_source[source]))
    return rows


_PREPARED_RE = re.compile(
    r"\b(?:tested|fully\s+working|serviced|refurbished|professionally\s+cleaned|diagnostics?\s+checked)\b",
    re.I,
)


def _evidence_level(
    *, sold_count: int, sample: int, dispersion: float, sources: int, freshness: float
) -> str:
    """A descriptive evidence grade instead of an uncalibrated percentage.

    The grade states what actually supports the estimate: completed sales, the
    number of independent observations, how closely they agree, how many
    marketplaces contributed and how recent they are. It is deliberately
    conservative: without a completed sale the ceiling is "moderate".
    """
    if (
        sold_count >= 3
        and sample >= 8
        and dispersion <= 0.25
        and sources >= 2
        and freshness >= 0.45
    ):
        return "strong"
    if (
        (sold_count >= 1 and sample >= 5 and dispersion <= 0.35)
        or (sample >= 8 and dispersion <= 0.30 and sources >= 2 and freshness >= 0.5)
    ):
        return "moderate"
    if sample >= MIN_COMPS or sold_count >= 1:
        return "limited"
    return "insufficient"


def _evidence_sentence(
    level: str, sold_count: int, sample: int, sources: list[str]
) -> str:
    source_text = ", ".join(sources[:4]) if sources else "no marketplaces"
    if sold_count:
        base = f"{sold_count} completed sale(s) and {sample} comparable observation(s) across {source_text}"
    else:
        base = f"{sample} live asking observation(s) across {source_text}, with no completed sales"
    label = {
        "strong": "Strong evidence",
        "moderate": "Moderate evidence",
        "limited": "Limited evidence",
        "insufficient": "Insufficient evidence",
    }[level]
    return f"{label}: {base}."


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
    # New stock is not evidence of the used price. eBay in particular is full of
    # sealed, brand-new consoles and graphics cards priced above retail; pooling
    # those asks made a used PS5 Pro look worth £833 when the private used
    # market was asking £500–600. New asks are kept as a retail ceiling only.
    candidate_is_new = str(candidate_condition or "").lower() == "new"
    new_asking_rows = [
        c for c in asking_rows
        if str(c.get("condition_norm") or "").lower() == "new"
    ]
    # The basis is always the used market, even when the candidate is new.
    # Sealed stock is a different market: its asks are often above retail and
    # a single cheap new listing must not drag the used baseline down. New asks
    # set the retail ceiling and scale the condition premium instead.
    asking_rows = [
        c for c in asking_rows
        if str(c.get("condition_norm") or "").lower() != "new"
    ]
    sold_pool = _trim_pool(sold_rows)
    asking_pool = _cap_source_weight(
        _cap_concentration(_trim_pool(asking_rows, asking_only=True))
    )
    cex_pool = _trim_pool(cex_rows)
    sold_count = len(sold_pool)
    has_cex = bool(cex_pool)
    cex_factor = config.cex_private_factor
    cex_note = ""
    if has_cex and asking_pool:
        # CeX retail is a high-street price with a warranty. Converting it to
        # private resale with a fixed factor was wrong in both directions: it
        # overvalued consoles and undervalued handhelds. Where a real asking
        # market exists, use the observed relationship for this product.
        retail = [
            float(c["cex_retail"]) for c in cex_pool if c.get("cex_retail")
        ]
        asked = weighted_median(
            (c["adjusted_price"], c["weight"]) for c in asking_pool
        )
        if retail and asked:
            observed = asked / median(retail)
            cex_factor = round(clamp(observed, 0.40, 0.95), 3)
            for comp in cex_pool:
                if comp.get("cex_retail"):
                    comp["adjusted_price"] = round(
                        float(comp["cex_retail"]) * cex_factor, 2
                    )
            cex_note = (
                "CeX retail was converted to private-resale level using this product's "
                f"observed asking market (×{cex_factor:.2f}), not a fixed discount."
            )
    method = "insufficient_data"
    basis: list[dict[str, Any]] = []
    evidence_n = 0
    notes: list[str] = []
    if new_asking_rows:
        notes.append(
            f"{len(new_asking_rows)} new-condition asking price(s) were kept out of the "
            "used-market basis and used only as a retail ceiling."
        )

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
    distinct_sources = sorted(
        {
            str(comp.get("marketplace") or comp.get("source") or "")
            for comp in kept
            if comp.get("marketplace") or comp.get("source")
        }
    )

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
    # A "new" premium is only believable if the comparable market actually
    # contains stock in that condition. A new RTX 4060 priced from eight used
    # cards cannot carry a full +20% uplift; that produced the questionable
    # £327 estimate. Scale the uplift by observed condition support instead of
    # assuming it.
    condition_support = 0.0
    if condition_factor > 1.0 and (kept or new_asking_rows):
        candidate_floor = condition_factor - 0.03
        total_weight = sum(
            float(c["weight"]) for c in kept
        ) + sum(float(c["weight"]) for c in new_asking_rows) or 1.0
        supported = sum(
            float(c["weight"])
            for c in new_asking_rows
            if CONDITION_FACTORS.get(str(c.get("condition_norm") or "unknown"), 0.95)
            >= candidate_floor
        )
        condition_support = supported / total_weight
        if condition_support <= 0.02:
            # No new stock in the market: there is no evidence for a premium at
            # all. The item is priced at the used level rather than guessed up.
            condition_factor = 1.0
            notes.append(
                f"No {candidate_condition.replace('_', ' ')}-condition comparables were found; "
                "the estimate uses the used market without a condition uplift."
            )
        else:
            support_scale = min(1.0, condition_support / 0.30)
            raw_factor = condition_factor
            condition_factor = 1.0 + (raw_factor - 1.0) * (0.25 + 0.75 * support_scale)
            if condition_factor < raw_factor - 0.005:
                notes.append(
                    f"The {candidate_condition.replace('_', ' ')} condition uplift was reduced from "
                    f"{raw_factor:.2f}× to {condition_factor:.2f}× because only "
                    f"{condition_support:.0%} of the comparable pool is in that condition."
                )
    # A falling market should lower a conservative estimate, but only modestly:
    # one short-term move is not proof of a new price level. Rising markets are
    # not used to inflate an estimate.
    range_damping = 1.0
    if sold_count == 0 and trend_pct is not None and trend_pct <= -0.05:
        damping = max(0.85, 1.0 + (trend_pct * 0.35))
        range_damping = damping
        notes.append(
            f"The estimate was reduced by {abs(1 - damping):.0%} to reflect the recent downward asking trend."
        )
    resale = resale_good * condition_factor * range_damping
    # A used item cannot be worth more than the cheapest new stock of the same
    # product. The cap is a sanity check, not an estimate: it only binds when
    # the used pool itself contains near-new asks.
    new_ceiling = None
    # A single cheap "new" ask is not a retail ceiling; it is often a
    # mis-described used card or a stale listing. Require a small sample before
    # letting new stock cap the used estimate.
    if not candidate_is_new and len(new_asking_rows) >= 3:
        new_prices = sorted(
            float(c.get("price") or 0) for c in new_asking_rows if float(c.get("price") or 0) > 0
        )
        if new_prices:
            new_ceiling = round(median(new_prices) * 0.92, 2)
            if resale > new_ceiling:
                notes.append(
                    "The estimate was capped at 92% of the median new asking price; "
                    "a used item cannot be assumed to fetch more than new stock."
                )
                resale = new_ceiling
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
    # There is deliberately no flat asking-only ceiling. The previous engine
    # capped every estimate at 0.45 + 0.10 + 0.05 + 0.03 = 0.63, which is why no
    # opportunity in the feed could ever exceed 63%: it was arithmetic, not
    # uncertainty. The honest disclosure is the evidence *basis* (completed
    # sales vs asking prices vs a CeX retail anchor) plus the evidence level,
    # and the UI must not present either as a probability of resale.
    confidence_basis = {
        "sold_comps": "completed_sales",
        "mixed_comps": "one_completed_sale",
        "live_asking_comps": "asking_only",
        "cex_anchor": "cex_retail_only",
        "component_model": "modelled_component",
    }.get(method, "asking_only")
    if confidence_basis == "asking_only":
        notes.append(
            "The resale estimate is an asking-price estimate with no verified completed sale; "
            "it is not a prediction of a guaranteed sale price."
        )
    if len(kept) < 5:
        confidence *= 0.90
        notes.append("Small evidence set — treat this estimate cautiously.")
    if len(kept) < MIN_COMPS:
        confidence = min(confidence, 0.28)
        notes.append("Fewer than three usable comparables: insufficient evidence for a confident estimate.")
    if iqr_ratio > 2.5:
        confidence *= 0.72
        notes.append("Comparable prices are widely dispersed; the lower quartile is used to avoid inflation.")
    if iqr_ratio > 4.0:
        confidence *= 0.62
        notes.append("Extreme price dispersion — verify the exact model and condition before buying.")
    if candidate_flags and "condition_caution" in candidate_flags:
        confidence *= 0.92
    if candidate_flags and "laptop_gpu" in candidate_flags:
        confidence *= 0.94
    if candidate_condition == "faulty":
        notes.append("Faulty items are excluded from buy recommendations.")
    if cex_note:
        notes.append(cex_note)
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

    level = _evidence_level(
        sold_count=sold_count,
        sample=len(kept),
        dispersion=spread,
        sources=len(distinct_sources),
        freshness=freshness,
    )
    notes.append(_evidence_sentence(level, sold_count, len(kept), distinct_sources))

    # Prepared/tested items genuinely attract more than untested ones, but the
    # premium has to come from this product's own market. Comparing asks that
    # mention testing/service with the rest of the pool gives an observed ratio;
    # it is applied only to the maximum-profit asking price, only when at least
    # three listings sit on each side, and never by more than 15%.
    prepared_uplift = 0.0
    prepared = [
        c for c in asking_pool if _PREPARED_RE.search(str(c.get("title") or ""))
    ]
    untested = [c for c in asking_pool if c not in prepared]
    if len(prepared) >= 3 and len(untested) >= 3:
        prepared_median = median([float(c["adjusted_price"]) for c in prepared]) or 0.0
        untested_median = median([float(c["adjusted_price"]) for c in untested]) or 0.0
        if prepared_median > 0 and untested_median > 0:
            prepared_uplift = round(
                clamp(prepared_median / untested_median - 1.0, 0.0, 0.15), 4
            )
            if prepared_uplift >= 0.02:
                notes.append(
                    f"Listings described as tested or fully working ask "
                    f"{prepared_uplift:.0%} more than untested equivalents of this product "
                    f"({len(prepared)} vs {len(untested)} listings)."
                )

    def value_at(q: float) -> float:
        value = weighted_quantile(
            ((c["adjusted_price"], c["weight"]) for c in basis), q
        ) or resale_good
        return round(value * condition_factor * range_damping, 2)

    strategy_values = {
        "quick": value_at(0.24),
        "balanced": round(resale, 2),
        "max": (
            value_at(0.62)
            if range_supported and spread <= 0.45
            else round(resale * 1.05, 2)
        ),
    }
    strategy_values["max"] = max(strategy_values["max"], strategy_values["balanced"])
    strategy_values["quick"] = min(strategy_values["quick"], strategy_values["balanced"])
    ask_premium = {"quick": 1.00, "balanced": 1.03, "max": 1.07}
    strategies = {
        name: {
            "expected": round(value, 2),
            "ask": round(value * ask_premium[name], 2),
        }
        for name, value in strategy_values.items()
    }
    if prepared_uplift >= 0.02:
        strategies["max"]["ask"] = round(
            float(strategies["max"]["ask"]) * (1.0 + prepared_uplift), 2
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
        evidence_level=level,
        strategies=strategies,
        sources={name: sum(1 for c in kept if (c.get('marketplace') or c.get('source')) == name)
                 for name in distinct_sources},
        resale_good=round(resale, 2),
        # The central value and the range must describe the same distribution.
        # Storing the un-conditioned quantile next to an adjusted estimate
        # produced ranges whose low was higher than the central estimate.
        low=round(min(resale, (p25 or resale) * condition_factor * range_damping), 2),
        high=round(max(resale, (p75 or resale) * condition_factor * range_damping), 2),
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
        ci_low=round(min(resale, (p10 or p25) * condition_factor * range_damping), 2),
        ci_high=round(max(resale, (p90 or p75) * condition_factor * range_damping), 2),
        trend_pct=round(trend_pct, 4) if trend_pct is not None else None,
        trend_note=trend_note,
        market_values=[round(value, 2) for value in values],
        confidence_basis=confidence_basis,
        calibration_active=bool(getattr(config, "calibration_active", False)),
    )


class ValuationEngine:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.pricing = PricingDatabase(db)
        self._comp_cache: dict[tuple[str, str], list[dict[str, Any]]] | None = None
        self._rejected_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._estimates: dict[tuple[str, str], Any] = {}
        self._estimate_conditions: dict[tuple[str, str], str] = {}
        self._snapshot_at: dict[str, str] = {}
        self._snapshot_cache: dict[str, str | None] = {}

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
                   evidence_confidence, source_id, seller, raw_json
            FROM (
                SELECT l.id, l.title, l.price, l.condition_norm, l.marketplace, l.url,
                       l.last_seen, 0 AS is_sold, l.marketplace AS source,
                       l.marketplace AS candidate_marketplace, 0 AS postage, NULL AS notes,
                       0 AS verified, 1.0 AS evidence_confidence, 'listing:' || l.id AS source_id,
                       COALESCE(l.seller, '') AS seller, l.raw_json
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
                   1.0 AS evidence_confidence, o.source_id, o.observed_at,
                   COALESCE(l.seller, '') AS seller, l.raw_json
            FROM market_observations o
            LEFT JOIN listings l ON l.marketplace=o.marketplace
                AND ('listing:' || l.id)=o.source_id
            WHERE o.product_key = ? AND (o.kind = ? OR o.kind IS NULL)
              AND o.observed_at >= ? AND o.invalidated_at IS NULL
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
                   evidence_confidence, source_id, COALESCE(seller, '') AS seller,
                   NULL AS raw_json
            FROM market_comps
            WHERE product_key = ? AND (kind = ? OR kind IS NULL) AND observed_at >= ?
            ORDER BY is_sold DESC, observed_at DESC LIMIT 120
            """,
            (product_key, kind, hours_ago(24 * 180)),
        )
        combined = rows + observations + external
        # Identity is checked before any price can influence the estimate. A
        # similar title is not evidence that the same product is being sold.
        accepted, rejected = split_pool(combined, kind=kind, product_key=product_key)
        if self._comp_cache is not None:
            self._comp_cache[cache_key] = accepted
            self._rejected_cache[cache_key] = rejected
        return accepted

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
        identity_confidence = float(listing.get("identification_confidence") or 0)
        identity_level = str(
            listing.get("identity_level") or identity_level_for(identity_confidence)
        )
        method = "insufficient_data"
        notes: list[str] = []
        estimate: MarketEstimate | None = None
        score = 0.0
        rank_tier = "weak"
        rank_components: dict[str, float] = {}

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
            estimate.rejected = self._rejected_cache.get((str(product_key), str(kind)), [])[:20]
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

        # What goes into permanent product-price memory must describe the
        # product, not one listing's missing controller or a learned flip
        # adjustment. Those are applied to this deal below; snapshotting them
        # would make the next listing of the same product inherit the discount.
        market_estimate = copy.deepcopy(estimate) if estimate else None

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
            for option in estimate.strategies.values():
                for field_name in ("expected", "ask"):
                    if option.get(field_name):
                        option[field_name] = round(
                            float(option[field_name]) * contents_factor, 2
                        )
            notes.append(
                "Resale estimate reduced by 12% because the listing states that controllers, "
                "cables, charger or other included accessories are missing."
            )
        strategy_name = (
            config.resale_strategy if config.resale_strategy in {"quick", "balanced", "max"} else "balanced"
        )
        option = (estimate.strategies or {}).get(strategy_name) if estimate else None
        expected_resale = (
            option["expected"] if option else (estimate.resale_good if estimate else None)
        )
        initial_ask = option.get("ask") if option else expected_resale
        outcome_factor = config.outcome_factors.get(kind) if estimate else None
        if expected_resale and outcome_factor and abs(outcome_factor - 1.0) >= 0.005:
            expected_resale = round(expected_resale * outcome_factor, 2)
            initial_ask = round((initial_ask or expected_resale) * outcome_factor, 2)
            for name, values in (estimate.strategies or {}).items():
                for field_name in ("expected", "ask"):
                    if values.get(field_name):
                        values[field_name] = round(
                            float(values[field_name]) * outcome_factor, 2
                        )
            notes.append(
                f"Resale adjusted {abs(1 - outcome_factor):.0%} "
                f"{'down' if outcome_factor < 1 else 'up'} from your completed "
                f"{kind} flips. The original market estimate is unchanged in history."
            )
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
        max_bid = None
        initial_ask = expected_resale
        required_roi = config.min_roi
        strategy_name = getattr(config, "resale_strategy", "balanced")
        holding_days = {"quick": 7, "balanced": 16, "max": 30}.get(strategy_name, 16)
        holding_days = round(
            holding_days
            * {"laptop": 1.25, "pc": 1.35, "console": 1.0, "phone": 0.8, "gpu": 0.9}
            .get(kind, 1.0),
            1,
        )
        score = 0.0
        is_deal = False
        rank_tier = "weak"
        rank_components = {}
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
            required_roi = (
                config.min_roi
                if purchase_total < config.high_value_threshold
                else max(config.min_roi, config.min_roi_high_value)
            )

            def proceeds_at(resale_value: float) -> float:
                fees = (
                    resale_value * config.ebay_fvf + config.ebay_fixed_fee
                    if channel == "ebay" else 0.0
                )
                return (
                    resale_value - fees - config.resale_shipping
                    - config.packaging_cost - config.collection_cost - config.other_costs
                )

            def net_at(resale_value: float) -> float:
                return round(proceeds_at(resale_value) - purchase_total, 2)

            def price_ceiling() -> float:
                """The most it is worth paying, honouring both thresholds.

                A bid/offer at this level still clears the minimum profit and the
                required return on the capital actually tied up.
                """
                net_of_purchase_costs = proceeds_at(expected_resale) - purchase_shipping
                by_profit = net_of_purchase_costs - max(config.min_profit, expected_resale * 0.08)
                by_roi = net_of_purchase_costs / (1.0 + required_roi)
                return round(max(0.0, min(by_profit, by_roi)), 2)

            max_bid = price_ceiling()
            net_profit = net_at(expected_resale)
            if estimate and estimate.range_supported and estimate.low is not None:
                net_profit_conservative = net_at(estimate.low)
            if estimate and estimate.range_supported and estimate.high is not None:
                net_profit_optimistic = net_at(estimate.high)
            roi = round(net_profit / purchase_total, 4) if purchase_total > 0 else None
            gap = (expected_resale - purchase_total) / expected_resale
            confidence = estimate.confidence if estimate else 0.0
            score = 0.0

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
            score, rank_tier, rank_components = _rank_opportunity(
                net_profit=net_profit,
                roi=roi,
                expected_resale=expected_resale,
                purchase_total=purchase_total,
                net_profit_conservative=net_profit_conservative,
                evidence_level=estimate.evidence_level if estimate else "insufficient",
                identity_confidence=float(listing.get("identification_confidence") or 0),
                risk_score=risk.score,
                holding_days=float(holding_days),
            )
            if net_profit_conservative is not None and net_profit_conservative <= 0:
                reasons.append(
                    f"Conservative downside is a loss of £{abs(net_profit_conservative):,.0f} "
                    "at the lower end of the supported resale range"
                )
            if estimate and estimate.confidence_basis == "asking_only":
                reasons.append(
                    "Resale is estimated from live asking prices, not verified completed sales"
                )
            is_deal = (
                net_profit >= config.min_profit
                and (roi or 0) >= required_roi
                and confidence >= config.min_confidence
                and not listing.get("is_faulty")
                and not listing.get("is_wanted")
                and expected_resale > purchase_total
                and (estimate.sample_size if estimate else 0) >= 4
                and float(listing.get("identification_confidence") or 0) >= 0.55
                and identity_level in {"limited", "moderate", "strong"}
                and risk.level != "high"
                and not listing.get("is_bundle")
                # An unstated storage, memory or chip variant cannot be valued
                # precisely, so it never enters the automatic feed even when
                # the price is attractive. The listing is still visible with
                # its variant warning; it is no longer a manual-review chore.
                and "variant_unclear" not in flags
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
            if (roi or 0) < required_roi:
                tier = (
                    "higher-value capital"
                    if purchase_total >= config.high_value_threshold else "standard"
                )
                reasons.append(
                    f"ROI below the {required_roi:.0%} threshold for {tier} purchases"
                )
            if net_profit < config.min_profit:
                reasons.append(f"Profit below the £{config.min_profit:.0f} threshold")
            if confidence < config.min_confidence:
                reasons.append(f"Confidence below the {config.min_confidence:.0%} threshold")

        if market_estimate is not None and product_key:
            slot = (str(product_key), str(kind))
            condition = str(listing.get("condition_norm") or "unknown")
            # Price memory should describe the product's used market, not
            # whichever listing happened to be evaluated last. If the key has
            # any used listing, its estimate replaces a new-condition one.
            previous = self._estimate_conditions.get(slot)
            if previous is None or (previous == "new" and condition != "new"):
                self._estimates[slot] = market_estimate
                self._estimate_conditions[slot] = condition
            key_text = str(product_key)
            if key_text not in self._snapshot_cache:
                snapshot = self.pricing.latest(key_text)
                self._snapshot_cache[key_text] = (
                    str(snapshot["refreshed_at"])
                    if snapshot and snapshot.get("refreshed_at") else None
                )
            if self._snapshot_cache[key_text]:
                self._snapshot_at[key_text] = self._snapshot_cache[key_text] or ""

        breakdown = {
            "purchase": {
                "price": round(price, 2),
                "shipping": round(purchase_shipping, 2),
                "buyer_fee": round(buyer_fee, 2),
                "total": purchase_total,
            },
            "resale": {
                "channel": channel_label,
                "strategy": strategy_name,
                "expected": expected_resale,
                "initial_ask": initial_ask,
                "strategies": estimate.strategies if estimate else {},
                "holding_days": holding_days,
                "max_bid": max_bid,
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
            "rejected_comps": estimate.rejected if estimate else [],
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
                "level": estimate.evidence_level if estimate else "insufficient",
                "basis": estimate.confidence_basis if estimate else "asking_only",
                "calibrated": bool(estimate.calibration_active) if estimate else False,
                "sources": estimate.sources if estimate else {},
                # An internal evidence score used for gating and ordering. It is
                # not a probability and the UI shows the level instead unless a
                # calibration against completed sales is active.
                "score": estimate.confidence if estimate else 0.0,
                "confidence": estimate.confidence if estimate else 0.0,
                "confidence_low": estimate.ci_low if estimate else None,
                "confidence_high": estimate.ci_high if estimate else None,
                "trend_pct": estimate.trend_pct if estimate else None,
                "trend_note": estimate.trend_note if estimate else "",
                "rejected_count": len(estimate.rejected) if estimate else 0,
            },
            "identity": {
                "level": identity_level,
                "confidence": round(identity_confidence, 3),
                "basis": list(listing.get("identity_basis") or []),
                "product_key": product_key,
                "product_name": listing.get("product_name"),
            },
            "ranking": {
                "score": score,
                "tier": rank_tier,
                "components": rank_components,
                "note": (
                    "Ranking weighs profit, capital, evidence, identity, risk and resale "
                    "time. It is not a probability of selling."
                ),
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
            "max_bid": max_bid,
            "resale_strategy": strategy_name,
            "resale_quick": (estimate.strategies.get("quick", {}) or {}).get("expected") if estimate else None,
            "resale_balanced": (estimate.strategies.get("balanced", {}) or {}).get("expected") if estimate else None,
            "resale_max": (estimate.strategies.get("max", {}) or {}).get("expected") if estimate else None,
            "evidence_level": estimate.evidence_level if estimate else "insufficient",
            "valuation_basis": estimate.confidence_basis if estimate else "asking_only",
            "evidence_score": round(estimate.confidence, 3) if estimate else 0.0,
            "identity_level": identity_level,
            "identity_confidence": round(identity_confidence, 3),
            "rank_tier": rank_tier,
            "rank_score": score,
            "estimated_days": holding_days if expected_resale else None,
            "valuation_refreshed_at": self._snapshot_at.get(str(product_key or "")),
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

    def _pool_signatures(self, listings: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
        """Fingerprint every comparable pool from grouped aggregates.

        A product key is re-evaluated only when its pool composition, prices or
        evidence actually changed. This keeps a scan from recomputing ~20k
        listings when one marketplace returned a few new items.
        """
        parts: dict[tuple[str, str], list[str]] = defaultdict(list)
        kinds: dict[str, set[str]] = defaultdict(set)
        for row in self.db.query(
            """
            SELECT product_key, listing_kind,
                   COALESCE(condition_norm, 'unknown') AS condition_norm,
                   COUNT(*) AS n, ROUND(SUM(price), 2) AS total
            FROM listings
            WHERE status='active' AND last_seen >= ? AND product_key IS NOT NULL
              AND is_faulty=0 AND is_wanted=0 AND is_bundle=0
              AND COALESCE(condition_norm, 'unknown') != 'faulty'
              AND price >= 5 AND price <= 25000
            GROUP BY product_key, listing_kind, condition_norm
            """,
            (hours_ago(24 * 30),),
        ):
            key = (row["product_key"], row["listing_kind"])
            parts[key].append(f"L|{row['condition_norm']}|{row['n']}|{row['total']}")
            kinds[row["product_key"]].add(row["listing_kind"])

        def apply(product_key: str, group_kind: str, piece: str) -> None:
            targets = [group_kind] if group_kind else list(kinds.get(product_key, ()))
            for kind in targets:
                key = (product_key, kind)
                if key in parts:
                    parts[key].append(piece)

        for row in self.db.query(
            """
            SELECT product_key, COALESCE(kind, '') AS kind,
                   COUNT(*) AS n, ROUND(SUM(price), 2) AS total
            FROM market_observations
            WHERE observed_at >= ? AND invalidated_at IS NULL
            GROUP BY product_key, kind
            """,
            (hours_ago(24 * 90),),
        ):
            apply(
                row["product_key"], row["kind"],
                f"O|{row['kind']}|{row['n']}|{row['total']}",
            )

        for row in self.db.query(
            """
            SELECT product_key, COALESCE(kind, '') AS kind,
                   COUNT(*) AS n, ROUND(SUM(price), 2) AS total,
                   MAX(COALESCE(updated_at, created_at, observed_at)) AS latest
            FROM market_comps
            WHERE observed_at >= ?
            GROUP BY product_key, kind
            """,
            (hours_ago(24 * 180),),
        ):
            apply(
                row["product_key"], row["kind"],
                f"C|{row['kind']}|{row['n']}|{row['total']}|{row['latest']}",
            )

        for row in self.db.query(
            """
            SELECT product_key, sell_price, cash_price, status, fetched_at
            FROM external_quotes WHERE source = 'cex'
            """
        ):
            apply(
                row["product_key"], "",
                f"X|{row['sell_price']}|{row['cash_price']}|{row['status']}|{row['fetched_at']}",
            )

        # A complete PC with too few complete-system comparables is valued from
        # its GPU pool, so that dependency belongs in the PC key's signature.
        dependencies: dict[tuple[str, str], set[str]] = defaultdict(set)
        for listing in listings:
            if listing.get("listing_kind") != "pc" or not listing.get("product_key"):
                continue
            try:
                attrs = json.loads(listing.get("attrs_json") or "{}")
            except (TypeError, ValueError):
                attrs = {}
            gpu_key = attrs.get("gpu_key") if isinstance(attrs, dict) else None
            if gpu_key:
                dependencies[(listing["product_key"], "pc")].add(str(gpu_key))

        signatures: dict[tuple[str, str], str] = {}
        for key, pieces in parts.items():
            extra = list(pieces)
            for gpu_key in sorted(dependencies.get(key, ())):
                gpu_parts = parts.get((gpu_key, "gpu"))
                if gpu_parts:
                    extra.append("G|" + _fingerprint(sorted(gpu_parts)))
            signatures[key] = _fingerprint(sorted(extra))
        return signatures

    def refresh(self, limit: int = 100000, full: bool = False) -> dict[str, int]:
        from .calibration import build_calibration

        from .flips import resale_factors

        config = ValuationConfig.from_db(self.db)
        calibration = build_calibration(self.db)
        config.category_factors = dict(calibration.category_factors)
        config.calibration_active = calibration.active
        config.calibration_note = calibration.reason
        # Learn slowly from completed flips. The factor only exists after at
        # least three sales of the same kind, and it is clamped in flips.py.
        config.outcome_factors = resale_factors(self.db)
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
        decisions = {
            int(row["listing_id"]): row["decision"]
            for row in self.db.query("SELECT listing_id, decision FROM review_decisions")
        }
        for listing in rows:
            listing["flags_list"] = json.loads(listing.get("flags") or "[]") if listing.get("flags") else []
            listing["attrs_json"] = listing.get("attrs")
            listing["review_decision"] = decisions.get(int(listing["id"]), "")

        fingerprints = {int(row["id"]): _listing_fingerprint(row) for row in rows}
        stored = {
            int(row["listing_id"]): row["input_fingerprint"]
            for row in self.db.query("SELECT listing_id, input_fingerprint FROM deals")
        }
        previous_keys = {
            (row["product_key"], row["kind"]): row["signature"]
            for row in self.db.query("SELECT product_key, kind, signature FROM valuation_keys")
        }
        signatures = self._pool_signatures(rows)
        changed_keys = {
            key for key, signature in signatures.items()
            if previous_keys.get(key) != signature
        }
        config_signature = _config_fingerprint(config)
        state = {
            row["key"]: row["value"]
            for row in self.db.query("SELECT key, value FROM valuation_state")
        }
        full = bool(
            full
            or config_signature != state.get("config_signature")
            or (age_hours(state.get("last_full_at")) or 0) >= FULL_REFRESH_HOURS
        )
        if full:
            targets = rows
        else:
            targets = []
            for listing in rows:
                listing_id = int(listing["id"])
                key = (listing.get("product_key"), listing.get("listing_kind"))
                if key in changed_keys or stored.get(listing_id) != fingerprints[listing_id]:
                    targets.append(listing)
            # A product whose last valuation is older than the reuse window is
            # queued for refresh even when none of its listing fields changed.
            # Deals and ending auctions come first; without this the engine
            # would keep serving week-old prices until the six-hour full pass.
            stale_keys = {
                str(row["product_key"])
                for row in self.pricing.stale_products(limit=150)
            }
            if stale_keys:
                queued = {int(listing["id"]) for listing in targets}
                for listing in rows:
                    listing_id = int(listing["id"])
                    if listing_id in queued:
                        continue
                    if str(listing.get("product_key") or "") in stale_keys:
                        targets.append(listing)
                        queued.add(listing_id)

        evaluated = deals = review = 0
        self._comp_cache = {}
        self._rejected_cache = {}
        self._estimates = {}
        self._estimate_conditions = {}
        self._snapshot_cache = {}
        self._snapshot_at = {}
        for listing in targets:
            result = self.evaluate(listing, config)
            self.db.execute(
                """
                INSERT INTO deals(listing_id, product_key, kind, expected_resale, resale_low,
                    resale_high, confidence, sample_size, valuation_method, purchase_total,
                    resale_fees, resale_shipping, packaging, net_profit,
                    net_profit_conservative, net_profit_optimistic, roi, score, is_deal,
                    needs_review, review_reason, risk_score, risk_level, risk_reasons,
                    reasons, breakdown, computed_at, input_fingerprint,
                    resale_strategy, resale_quick, resale_balanced, resale_max,
                    evidence_level, max_bid, estimated_days, valuation_refreshed_at,
                    identity_level, valuation_basis, evidence_score, rank_score, rank_tier)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    breakdown=excluded.breakdown, computed_at=excluded.computed_at,
                    input_fingerprint=excluded.input_fingerprint,
                    resale_strategy=excluded.resale_strategy,
                    resale_quick=excluded.resale_quick,
                    resale_balanced=excluded.resale_balanced,
                    resale_max=excluded.resale_max,
                    evidence_level=excluded.evidence_level,
                    max_bid=excluded.max_bid,
                    estimated_days=excluded.estimated_days,
                    valuation_refreshed_at=excluded.valuation_refreshed_at,
                    identity_level=excluded.identity_level,
                    valuation_basis=excluded.valuation_basis,
                    evidence_score=excluded.evidence_score,
                    rank_score=excluded.rank_score,
                    rank_tier=excluded.rank_tier
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
                    fingerprints[int(result["listing_id"])],
                    result["resale_strategy"], result["resale_quick"],
                    result["resale_balanced"], result["resale_max"],
                    result["evidence_level"], result["max_bid"],
                    result["estimated_days"], result["valuation_refreshed_at"],
                    result["identity_level"], result["valuation_basis"],
                    result["evidence_score"], result["rank_score"], result["rank_tier"],
                ),
            )
            evaluated += 1
            deals += result["is_deal"]
            review += result["needs_review"]
        # Every product valuation is appended to the permanent price memory.
        # Reuse comes from this table, so a product researched on eBay is
        # available to a Vinted listing of the same identity.
        snapshots = 0
        for (product_key, kind), estimate in self._estimates.items():
            if self.pricing.record(product_key, kind, estimate):
                snapshots += 1
        if self._estimates:
            # One set-based statement. Updating key by key scanned the whole
            # deals table once per product and turned a 25-second pass into
            # minutes on a 70k-row database.
            self.db.execute(
                """
                UPDATE deals SET valuation_refreshed_at = (
                    SELECT s.refreshed_at FROM valuation_snapshots s
                    WHERE s.product_key = deals.product_key
                      AND s.invalidated_at IS NULL
                    ORDER BY s.id DESC LIMIT 1
                )
                WHERE product_key IN (
                    SELECT s.product_key FROM valuation_snapshots s
                    WHERE s.product_key = deals.product_key
                      AND s.invalidated_at IS NULL
                )
                """
            )
        self._estimates = {}
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
        now = iso()
        stored_keys = signatures if full else {
            key: signatures[key] for key in changed_keys
        }
        if stored_keys:
            self.db.executemany(
                """
                INSERT INTO valuation_keys(product_key, kind, signature, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(product_key, kind) DO UPDATE SET
                    signature=excluded.signature, updated_at=excluded.updated_at
                """,
                [
                    (product_key, kind, signature, now)
                    for (product_key, kind), signature in stored_keys.items()
                ],
            )
        state_values = [("config_signature", config_signature, now), ("last_refresh_at", now, now)]
        if full:
            state_values.append(("last_full_at", now, now))
        self.db.executemany(
            """
            INSERT INTO valuation_state(key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            state_values,
        )
        self._comp_cache = None
        return {
            "evaluated": evaluated,
            "deals": deals,
            "review": review,
            "observed": observed,
            "keys": len(changed_keys),
            "full": int(full),
            "snapshots": snapshots,
        }
