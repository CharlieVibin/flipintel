"""Explainable listing-risk scoring.

A low price is a *reason to verify*, not proof of fraud. The score combines
market-position evidence with independent signals (condition contradictions,
seller reputation, ambiguous identity, accessories, missing media and so on).
Price-only evidence is deliberately capped so it can never produce a high-risk
label by itself.

The output is shown to the user with reasons and is used to route uncertain
listings to the review queue rather than the automatic deal feed.
"""
from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .identification import CONDITION_FACTORS
from .utils import clamp, parse_iso


@dataclass
class RiskAssessment:
    score: float = 0.0
    level: str = "low"
    reasons: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    price_percentile: float | None = None
    market_ratio: float | None = None
    review_recommended: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(clamp(self.score, 0, 100), 1),
            "level": self.level,
            "reasons": self.reasons,
            "signals": self.signals,
            "price_percentile": (
                round(self.price_percentile, 3) if self.price_percentile is not None else None
            ),
            "market_ratio": round(self.market_ratio, 3) if self.market_ratio is not None else None,
            "review_recommended": self.review_recommended,
        }


def _seller_feedback(listing: dict[str, Any]) -> tuple[float | None, int | None, str | None]:
    try:
        raw = json.loads(listing.get("raw_json") or "{}")
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    percentage = raw.get("seller_feedback")
    score = raw.get("seller_feedback_score")
    registration = raw.get("seller_registration")
    try:
        percentage_value = float(str(percentage).replace("%", "").strip()) if percentage is not None else None
    except (TypeError, ValueError):
        percentage_value = None
    try:
        score_value = int(float(score)) if score is not None else None
    except (TypeError, ValueError):
        score_value = None
    return percentage_value, score_value, str(registration) if registration else None


def assess_listing(
    listing: dict[str, Any],
    estimate: Any | None,
    *,
    expected_resale: float | None = None,
    purchase_total: float | None = None,
) -> RiskAssessment:
    flags = set(listing.get("flags_list") or [])
    price = float(listing.get("price") or 0)
    purchase = float(purchase_total if purchase_total is not None else price)
    expected = float(expected_resale or 0)
    condition = str(listing.get("condition_norm") or "unknown")
    condition_factor = CONDITION_FACTORS.get(condition, 0.95)
    signals: list[tuple[str, float, str]] = []
    price_percentile: float | None = None
    market_ratio: float | None = None

    comps = list(getattr(estimate, "comps", []) or []) if estimate is not None else []
    prices = sorted(
        float(comp.get("adjusted_price") or 0)
        for comp in comps
        if float(comp.get("adjusted_price") or 0) > 0
    )
    # Only use a market percentile when the available pool is large enough to
    # make the position meaningful.
    if len(prices) >= 5 and price > 0:
        candidate = price / max(0.2, condition_factor)
        index = bisect.bisect_left(prices, candidate)
        price_percentile = index / len(prices)
        market_median = prices[len(prices) // 2]
        ratio_to_market = candidate / market_median if market_median > 0 else 1.0
        market_ratio = ratio_to_market
        # Being the cheapest listing in a tight market is often a genuine
        # bargain. Only a *large* gap below the market median is a meaningful
        # risk signal, and it is still only one signal.
        # Compare with the median, not the cheapest observation. One junk or
        # scam asking price must not shift the whole risk calculation. The
        # percentile is still reported for context, but the ratio is the signal.
        if ratio_to_market <= 0.70:
            points = 30.0 if ratio_to_market <= 0.50 else 18.0
            signals.append((
                "price_anomaly",
                points,
                "Asking price is far below the comparable UK market median; "
                "verify the exact item, included parts and condition before treating this as a bargain.",
            ))

    if expected > 0 and purchase < expected * 0.30:
        signals.append((
            "extreme_discount",
            25.0,
            "The all-in cost is less than 30% of the modelled resale value. This may be a different "
            "variant, an accessory, a faulty item or a misleading listing.",
        ))

    if "condition_contradiction" in flags:
        signals.append((
            "condition_contradiction",
            20.0,
            "The title and description disagree about condition (for example new/sealed versus used/opened).",
        ))
    if "faulty_or_parts" in flags or "parts_only" in flags:
        signals.append((
            "faulty_or_parts",
            38.0,
            "The listing mentions faults, spares or parts. It cannot be valued as working hardware.",
        ))
    if "accessory_or_box" in flags:
        signals.append((
            "accessory_or_box",
            35.0,
            "The title suggests an accessory, packaging or compatible item rather than the complete device.",
        ))
    if "compatibility_accessory" in flags:
        signals.append((
            "compatibility_accessory",
            30.0,
            "The model name may be mentioned only for compatibility, not as the item being sold.",
        ))
    if "model_unclear" in flags or "multiple_models" in flags or "multiple_gpus" in flags:
        signals.append((
            "ambiguous_model",
            14.0,
            "The exact model or variant is ambiguous, so the comparable market may not match this item.",
        ))
    if "variant_unclear" in flags:
        signals.append((
            "variant_unclear",
            12.0,
            "Storage, memory or chip variant is not stated. The comparable pool may mix "
            "configuration levels with materially different resale values.",
        ))
    if "component_bundle" in flags or "bundle" in flags:
        signals.append((
            "bundle",
            10.0,
            "This is a bundle or multi-item listing; the asking price cannot be assigned to one product.",
        ))
    if "contents_incomplete" in flags:
        signals.append((
            "contents_incomplete",
            8.0,
            "The listing says controllers, cables, charger or a dock are missing; resale value is "
            "reduced and the buyer must budget for replacements.",
        ))
    if listing.get("is_auction"):
        signals.append((
            "auction",
            5.0,
            "The listing is an auction; the final price is not yet known.",
        ))
    if not listing.get("image_url"):
        signals.append((
            "no_image",
            4.0,
            "No listing image is available, which makes the item harder to verify.",
        ))

    percentage, feedback_score, registration = _seller_feedback(listing)
    if percentage is not None and percentage < 95:
        points = 22.0 if percentage < 90 else 12.0
        signals.append((
            "seller_feedback",
            points,
            f"Seller feedback is {percentage:g}%, below the 95% level normally expected for a low-risk purchase.",
        ))
    elif feedback_score is not None and feedback_score < 5:
        signals.append((
            "seller_history",
            12.0,
            "The seller has very little feedback history.",
        ))
    if registration:
        registered = parse_iso(registration)
        if registered:
            if registered.tzinfo is None:
                registered = registered.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - registered).days
            if age_days < 60:
                signals.append((
                    "new_seller",
                    10.0,
                    "The seller account is less than 60 days old.",
                ))

    score = sum(points for _, points, _ in signals)
    signal_keys = [key for key, _, _ in signals]
    price_only = set(signal_keys) <= {"price_anomaly", "extreme_discount"}
    if price_only:
        # A low price alone is never enough for a high-risk label.
        score = min(score, 45.0)
    if not signals:
        score = 0.0
    score = clamp(score, 0, 100)
    if score >= 60 and len(signal_keys) >= 2:
        level = "high"
    elif score >= 30 or "extreme_discount" in signal_keys:
        level = "medium"
    else:
        level = "low"
    sold_support = int(getattr(estimate, "sold_count", 0) or 0) if estimate is not None else 0
    deep_price_anomaly = bool(
        "price_anomaly" in signal_keys
        and sold_support == 0
        and market_ratio is not None
        and market_ratio <= 0.65
    )
    review_recommended = (
        level == "high"
        or (price_only and score >= 30)
        or (level == "medium" and len(signal_keys) >= 2 and not price_only)
        or "variant_unclear" in signal_keys
        # A price far below the observed market is not fraud on its own, but it
        # is exactly the case a human should verify before it ranks as a deal.
        # The listing stays low/medium risk; it simply leaves the automatic feed.
        or deep_price_anomaly
    )
    reasons = [reason for _, _, reason in signals]
    return RiskAssessment(
        score=score,
        level=level,
        reasons=reasons,
        signals=signal_keys,
        price_percentile=price_percentile,
        market_ratio=market_ratio,
        review_recommended=review_recommended,
    )
