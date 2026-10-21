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
SUPPORTED_KINDS = {"gpu", "cpu", "ram", "ssd", "motherboard", "pc", "laptop", "console", "monitor", "phone", "tablet"}


@dataclass
class ValuationConfig:
    min_profit: float = 20.0
    min_roi: float = 0.12
    min_confidence: float = 0.42
    ebay_fvf: float = 0.1322
    ebay_fixed_fee: float = 0.30
    resale_shipping: float = 4.50
    packaging_cost: float = 1.00
    collection_cost: float = 0.0
    vinted_buyer_fee_pct: float = 0.05
    vinted_buyer_fee_fixed: float = 0.70
    vinted_shipping: float = 3.49
    asking_to_sold_factor: float = 0.90
    cex_private_factor: float = 0.80

    @classmethod
    def from_db(cls, db: Database) -> "ValuationConfig":
        values = db.get_settings()
        def f(key: str, default: float) -> float:
            try:
                return float(values.get(key, default))
            except (TypeError, ValueError):
                return default
        return cls(
            min_profit=f("min_profit", 20.0),
            min_roi=f("min_roi", 0.12),
            min_confidence=f("min_confidence", 0.42),
            ebay_fvf=f("ebay_fvf", 0.1322),
            ebay_fixed_fee=f("ebay_fixed_fee", 0.30),
            resale_shipping=f("resale_shipping", 4.50),
            packaging_cost=f("packaging_cost", 1.00),
            collection_cost=f("collection_cost", 0.0),
            vinted_buyer_fee_pct=f("vinted_buyer_fee_pct", 0.05),
            vinted_buyer_fee_fixed=f("vinted_buyer_fee_fixed", 0.70),
            vinted_shipping=f("vinted_shipping", 3.49),
            asking_to_sold_factor=f("asking_to_sold_factor", 0.90),
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
    notes: list[str] = field(default_factory=list)


def _log_stats(values: list[float]) -> tuple[float, float]:
    logs = [math.log(v) for v in values if v > 0]
    if not logs:
        return 0.0, 0.0
    med = median(logs) or 0.0
    deviations = [abs(v - med) for v in logs]
    mad = median(deviations) or 0.0
    return med, mad * 1.4826  # robust sigma


def _clean_and_weight(rows: list[dict[str, Any]], config: ValuationConfig) -> list[dict[str, Any]]:
    """Condition-normalise rows and attach weights."""
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        price = float(row.get("price") or 0)
        if price <= 0:
            continue
        condition = row.get("condition_norm") or "unknown"
        factor = CONDITION_FACTORS.get(condition, 0.95)
        adjusted = price / factor
        is_sold = bool(row.get("is_sold"))
        if not is_sold:
            adjusted *= config.asking_to_sold_factor
        age = age_hours(row.get("observed_at") or row.get("last_seen"))
        recency = 0.65 + 0.35 * math.exp(-(age or 0) / (24 * 21))
        if is_sold:
            weight = 1.7
        else:
            weight = 0.85 if row.get("marketplace") != row.get("candidate_marketplace") else 0.7
            weight *= recency
        if row.get("source") == "cex":
            adjusted = price * config.cex_private_factor
            weight = 1.0
        cleaned.append(
            {
                **row,
                "adjusted_price": round(adjusted, 2),
                "weight": max(0.05, weight),
                "is_sold": is_sold,
            }
        )
    return cleaned


def _estimate_from_rows(
    rows: list[dict[str, Any]],
    config: ValuationConfig,
    ident_confidence: float,
    candidate_condition: str,
    candidate_flags: list[str] | None = None,
) -> MarketEstimate:
    cleaned = _clean_and_weight(rows, config)
    if not cleaned:
        return MarketEstimate(None, None, None, 0.0, 0, "insufficient_data",
                              notes=["No comparable listings have been observed yet."])

    adjusted_values = [c["adjusted_price"] for c in cleaned]
    rough_median = median(adjusted_values) or 0.0
    # Asymmetric outlier handling: overpriced asks are common, so trim the upper
    # tail; retain cheaper examples because they are evidence of achievable
    # value. Only absurd lows (box-only scams) are discarded.
    kept = [
        c for c in cleaned
        if c["adjusted_price"] <= rough_median * 3.5
        and c["adjusted_price"] >= max(5.0, rough_median * 0.08)
    ]
    excluded = len(cleaned) - len(kept)
    if len(kept) < MIN_COMPS:
        kept = cleaned
        excluded = 0
    values = [c["adjusted_price"] for c in kept]
    weights = [c["weight"] for c in kept]
    weighted_median_value = weighted_median(zip(values, weights))
    if not weighted_median_value:
        return MarketEstimate(None, None, None, 0.0, len(kept), "insufficient_data")

    log_med, sigma = _log_stats(values)
    sold_count = sum(1 for c in kept if c["is_sold"])
    asking_count = len(kept) - sold_count
    has_cex = any(c.get("source") == "cex" for c in kept)

    # Condition adjustment to the candidate: comps were normalised to "good".
    condition_factor = CONDITION_FACTORS.get(candidate_condition, 0.95)
    p25 = quantile(values, 0.25) or weighted_median_value * 0.85
    p75 = quantile(values, 0.75) or weighted_median_value * 1.15
    iqr_ratio = p75 / p25 if p25 > 0 else 99.0
    if sold_count >= 2:
        resale_good = weighted_median_value
    else:
        # With asking prices only, a lower weighted quantile is a more realistic
        # private-sale realisation than the median of wishful ask prices.
        resale_good = weighted_quantile(zip(values, weights), 0.38) or weighted_median_value
        if iqr_ratio > 2.5:
            resale_good = min(resale_good, p25)
    resale = resale_good * condition_factor
    spread = max(0.08, sigma)

    ident = clamp(ident_confidence, 0.0, 1.0)
    sample_score = clamp(len(kept) / 10.0, 0.0, 1.0)
    dispersion_score = 1.0 / (1.0 + 2.2 * spread)
    if sold_count >= 3:
        source_score, source_note = 1.0, "Backed by recently completed sales."
    elif sold_count > 0:
        source_score, source_note = 0.88, "Mix of completed sales and live asking prices."
    elif has_cex:
        source_score, source_note = 0.84, "CeX retail price used as an anchor, adjusted for private resale."
    else:
        source_score, source_note = 0.70, (
            "Live UK asking prices only; no sold-price feed is available, so the "
            "estimate is adjusted for negotiation and carries lower confidence."
        )
    freshness_values = []
    for comp in kept:
        age = age_hours(comp.get("observed_at") or comp.get("last_seen"))
        freshness_values.append(math.exp(-(age or 0) / (24 * 14)))
    freshness = sum(freshness_values) / len(freshness_values) if freshness_values else 0.0

    notes = [source_note]
    confidence = (
        0.32 * ident
        + 0.26 * sample_score
        + 0.18 * dispersion_score
        + 0.16 * source_score
        + 0.08 * freshness
    )
    if len(kept) < 5:
        confidence = min(confidence, 0.55)
    if len(kept) < MIN_COMPS:
        confidence = min(confidence, 0.30)
    if sold_count == 0:
        confidence = min(confidence, 0.68)
    if iqr_ratio > 2.5:
        confidence *= 0.72
        notes.append(
            "Asking prices are widely dispersed; the lower quartile is used to avoid inflated valuations."
        )
    if iqr_ratio > 4.0:
        confidence = min(confidence, 0.34)
    if candidate_flags and "condition_caution" in candidate_flags:
        confidence *= 0.92
    if candidate_flags and "laptop_gpu" in candidate_flags:
        confidence *= 0.94

    if excluded:
        notes.append(f"{excluded} outlier comparable(s) excluded.")
    if candidate_condition == "faulty":
        notes.append("Faulty items are excluded from buy recommendations.")
    if len(kept) < 5:
        notes.append("Small comparable sample — treat this estimate cautiously.")

    public_comps = []
    for comp in sorted(kept, key=lambda c: c["weight"], reverse=True)[:10]:
        public_comps.append(
            {
                "title": comp.get("title"),
                "price": round(float(comp.get("price") or 0), 2),
                "adjusted_price": comp["adjusted_price"],
                "marketplace": comp.get("marketplace") or comp.get("source"),
                "condition": comp.get("condition_norm") or "unknown",
                "is_sold": bool(comp.get("is_sold")),
                "url": comp.get("url"),
                "observed_at": comp.get("observed_at") or comp.get("last_seen"),
            }
        )

    method = "sold_comps" if sold_count >= 3 else ("mixed_comps" if sold_count else (
        "cex_anchor" if has_cex else "live_asking_comps"))

    return MarketEstimate(
        resale_good=round(resale_good, 2),
        low=round((p25 or resale) * condition_factor, 2),
        high=round((p75 or resale) * condition_factor, 2),
        confidence=round(clamp(confidence, 0.0, 0.98), 3),
        sample_size=len(kept),
        method=method,
        comps=public_comps,
        sold_count=sold_count,
        asking_count=asking_count,
        dispersion=round(spread, 3),
        notes=notes,
    )


class ValuationEngine:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- data gathering -----------------------------------------------------
    def _market_comps(self, product_key: str, kind: str, exclude_id: int) -> list[dict[str, Any]]:
        rows = self.db.query(
            """
            SELECT id, title, price, condition_norm, marketplace, url, last_seen AS observed_at,
                   is_sold, source, candidate_marketplace
            FROM (
                SELECT l.id, l.title, l.price, l.condition_norm, l.marketplace, l.url,
                       l.last_seen, 0 AS is_sold, l.marketplace AS source,
                       l.marketplace AS candidate_marketplace
                FROM listings l
                WHERE l.product_key = ? AND l.listing_kind = ? AND l.status = 'active'
                  AND l.last_seen >= ? AND l.id != ? AND l.is_faulty = 0 AND l.is_wanted = 0
                  AND l.is_bundle = 0 AND l.price >= 5 AND l.price <= 25000
                ORDER BY l.last_seen DESC LIMIT 250
            )
            """,
            (product_key, kind, hours_ago(24 * 30), exclude_id),
        )
        external = self.db.query(
            """
            SELECT id, title, price, condition_norm, marketplace, url, observed_at,
                   is_sold, source, '' AS candidate_marketplace
            FROM market_comps
            WHERE product_key = ? AND (kind = ? OR kind IS NULL) AND observed_at >= ?
            ORDER BY observed_at DESC LIMIT 80
            """,
            (product_key, kind, hours_ago(24 * 45)),
        )
        return rows + external

    def _gpu_fallback_comps(self, gpu_key: str) -> list[dict[str, Any]]:
        return self.db.query(
            """
            SELECT l.id, l.title, l.price, l.condition_norm, l.marketplace, l.url,
                   l.last_seen AS observed_at, 0 AS is_sold, l.marketplace AS source,
                   l.marketplace AS candidate_marketplace
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
            cex = self._cex_comp(product_key)
            if cex:
                rows.append(cex)
            estimate = _estimate_from_rows(
                rows,
                config,
                float(listing.get("identification_confidence") or 0),
                listing.get("condition_norm") or "unknown",
                flags,
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
                            "good", flags,
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
        # resale_good already has candidate condition applied in the estimate
        resale_fees = 0.0
        net_profit = None
        roi = None
        score = 0.0
        is_deal = False
        reasons: list[str] = []

        if expected_resale and expected_resale > 0:
            resale_fees = round(expected_resale * config.ebay_fvf + config.ebay_fixed_fee, 2)
            net_proceeds = round(
                expected_resale - resale_fees - config.resale_shipping - config.packaging_cost
                - config.collection_cost,
                2,
            )
            net_profit = round(net_proceeds - purchase_total, 2)
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
                estimate.confidence = min(0.55, round(estimate.confidence * 0.88, 3))
                reasons.append(
                    "Flagged for manual review: price is unusually far below the modelled resale value"
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
                and not suspicious_price
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
                "channel": "eBay UK (private seller)",
                "expected": expected_resale,
                "low": estimate.low if estimate else None,
                "high": estimate.high if estimate else None,
                "fees": resale_fees,
                "shipping": config.resale_shipping,
                "packaging": config.packaging_cost,
                "collection": config.collection_cost,
            },
            "comps": estimate.comps if estimate else [],
            "sample_size": estimate.sample_size if estimate else 0,
            "sold_count": estimate.sold_count if estimate else 0,
            "asking_count": estimate.asking_count if estimate else 0,
            "dispersion": estimate.dispersion if estimate else None,
            "valuation_notes": notes,
            "assumptions": {
                "asking_to_sold_factor": config.asking_to_sold_factor,
                "ebay_fvf": config.ebay_fvf,
                "ebay_fixed_fee": config.ebay_fixed_fee,
                "cex_private_factor": config.cex_private_factor,
            },
        }

        return {
            "listing_id": listing_id,
            "product_key": product_key,
            "kind": kind,
            "expected_resale": round(expected_resale, 2) if expected_resale else None,
            "resale_low": estimate.low if estimate else None,
            "resale_high": estimate.high if estimate else None,
            "confidence": estimate.confidence if estimate else 0.0,
            "sample_size": estimate.sample_size if estimate else 0,
            "valuation_method": method,
            "purchase_total": purchase_total,
            "resale_fees": resale_fees,
            "resale_shipping": config.resale_shipping if expected_resale else 0.0,
            "packaging": config.packaging_cost if expected_resale else 0.0,
            "net_profit": net_profit,
            "roi": roi,
            "score": score,
            "is_deal": int(is_deal),
            "reasons": reasons,
            "breakdown": breakdown,
            "computed_at": iso(),
        }

    # --- refresh ------------------------------------------------------------
    def refresh(self, limit: int = 4000) -> dict[str, int]:
        config = ValuationConfig.from_db(self.db)
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
        # Clear recommendations for listings that are no longer evaluated (for
        # example after identification rules tightened) before rebuilding them.
        self.db.execute("UPDATE deals SET is_deal = 0")
        evaluated = deals = 0
        for listing in rows:
            listing["flags_list"] = json.loads(listing.get("flags") or "[]") if listing.get("flags") else []
            listing["attrs_json"] = listing.get("attrs")
            result = self.evaluate(listing, config)
            self.db.execute(
                """
                INSERT INTO deals(listing_id, product_key, kind, expected_resale, resale_low,
                    resale_high, confidence, sample_size, valuation_method, purchase_total,
                    resale_fees, resale_shipping, packaging, net_profit, roi, score, is_deal,
                    reasons, breakdown, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(listing_id) DO UPDATE SET
                    product_key=excluded.product_key, kind=excluded.kind,
                    expected_resale=excluded.expected_resale, resale_low=excluded.resale_low,
                    resale_high=excluded.resale_high, confidence=excluded.confidence,
                    sample_size=excluded.sample_size, valuation_method=excluded.valuation_method,
                    purchase_total=excluded.purchase_total, resale_fees=excluded.resale_fees,
                    resale_shipping=excluded.resale_shipping, packaging=excluded.packaging,
                    net_profit=excluded.net_profit, roi=excluded.roi, score=excluded.score,
                    is_deal=excluded.is_deal, reasons=excluded.reasons,
                    breakdown=excluded.breakdown, computed_at=excluded.computed_at
                """,
                (
                    result["listing_id"], result["product_key"], result["kind"],
                    result["expected_resale"], result["resale_low"], result["resale_high"],
                    result["confidence"], result["sample_size"], result["valuation_method"],
                    result["purchase_total"], result["resale_fees"], result["resale_shipping"],
                    result["packaging"], result["net_profit"] or 0.0, result["roi"] or 0.0,
                    result["score"], result["is_deal"], json_dumps(result["reasons"]),
                    json_dumps(result["breakdown"]), result["computed_at"],
                ),
            )
            evaluated += 1
            deals += result["is_deal"]
        return {"evaluated": evaluated, "deals": deals}
