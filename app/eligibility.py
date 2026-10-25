"""Pre-feed eligibility: recommended, needs verification, or excluded.

Eligibility is decided from the physical product role, identity, risk and
valuation state. It is deliberately separate from ``is_deal``: a listing can
have a perfectly valid resale estimate and still be excluded because it is an
accessory, packaging, faulty, unprofitable or outside the supported product
classes. The deal feed and review queue read these values from the database;
the frontend is not the place where junk is hidden.
"""
from __future__ import annotations

import json
from typing import Any

RECOMMENDED = "recommended"
NEEDS_VERIFICATION = "needs_verification"
EXCLUDED = "excluded"

# Sale roles that are never a resale opportunity in their own right.
NON_PRODUCT_ROLES = {
    "accessory": "Accessory or part, not the complete product",
    "packaging": "Packaging only; the product is not included",
    "media": "Removable storage media or game, not the device it fits",
    "game": "Game software, not console hardware",
    "parts": "Spare parts or replacement component",
    "wanted": "Wanted advertisement, not a sale",
    "unknown": "The physical product could not be established",
}

# Flags that describe structural noise rather than a product identity.
NON_PRODUCT_FLAGS = {
    "accessory_or_box": "Accessory, compatibility item or packaging",
    "compatibility_accessory": "Advertised for compatibility with another product",
    "console_game": "Game software, not console hardware",
    "storage_media": "Removable storage media, not the device",
    "parts_only": "Spare parts or replacement component",
    "wanted_ad": "Wanted advertisement, not a sale",
    "contract_or_finance": "Contract or finance listing, not an outright sale",
    "multi_item": "Multiple different devices in one listing",
    "engineering_sample": "Engineering sample, not a retail product",
    "suspicious_capacity": "Implausible or unverifiable storage capacity",
    "capacity_range": "Variation listing advertises a capacity range, so the actual product is unknown",
    "unbranded_storage": "Generic unbranded portable storage; model and capacity cannot be verified",
}


def sale_role(listing: dict[str, Any]) -> str:
    role = str(listing.get("sale_role") or "").strip().lower()
    if role:
        return role
    raw = listing.get("attrs_json") or listing.get("attrs")
    if raw:
        try:
            attrs = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(attrs, dict):
                return str(attrs.get("sale_role") or "").strip().lower()
        except (TypeError, ValueError):
            pass
    flags = set(listing.get("flags_list") or [])
    if "storage_media" in flags:
        return "media"
    if "console_game" in flags:
        return "game"
    if "parts_only" in flags:
        return "parts"
    if "accessory_or_box" in flags or "compatibility_accessory" in flags:
        return "accessory"
    return ""


def assess(
    listing: dict[str, Any],
    *,
    is_deal: bool,
    needs_review: bool,
    expected_resale: float | None,
    purchase_total: float,
    net_profit: float | None,
    risk_level: str = "",
    review_resolved: bool = False,
    min_profit: float | None = None,
    min_roi: float | None = None,
    identity_level: str = "",
    confidence: float | None = None,
) -> tuple[str, str | None]:
    """Return ``(eligibility, reason)`` for one evaluated listing.

    This is the single authoritative gate. The deal feed, review queue,
    overview counters and Discord selection all read its persisted result, so
    an excluded listing cannot reappear through another endpoint.
    """
    flags = set(listing.get("flags_list") or [])
    role = sale_role(listing)

    if listing.get("is_wanted"):
        return EXCLUDED, NON_PRODUCT_ROLES["wanted"]
    if role in NON_PRODUCT_ROLES:
        return EXCLUDED, NON_PRODUCT_ROLES[role]
    for flag, reason in NON_PRODUCT_FLAGS.items():
        if flag in flags:
            return EXCLUDED, reason
    if listing.get("is_faulty"):
        return EXCLUDED, "Faulty or spares item; it cannot support a working-condition valuation"
    if listing.get("is_bundle"):
        return EXCLUDED, "Bundle or multi-item listing, not one comparable product"
    if listing.get("duplicate_of"):
        return EXCLUDED, "Duplicate of an already tracked listing for the same seller, item and price"
    if not listing.get("product_key"):
        return EXCLUDED, "Exact model could not be established, so no compatible valuation exists"
    if (
        expected_resale
        and expected_resale > 0
        and purchase_total > 0
        and purchase_total < expected_resale * 0.05
    ):
        # A £15 iPhone or £125 RTX 5090 is not a bargain to review; the listed
        # item is almost certainly not the claimed product or not the complete
        # item. Keep the record, but do not spend the user's review time on it.
        return EXCLUDED, "Price is under 5% of modelled resale; identity or legitimacy cannot be trusted"

    if is_deal:
        return RECOMMENDED, None

    if expected_resale is None:
        return EXCLUDED, "Insufficient compatible evidence for a resale estimate"
    if net_profit is None or net_profit <= 0 or expected_resale <= purchase_total:
        return EXCLUDED, "No expected profit after purchase, fees and delivery"
    if needs_review:
        # A review card costs the user time, so it must be a plausible deal
        # that one answer can unlock. Thin margins, a family-level guess or a
        # weak estimate are internal research cases, not manual tasks.
        roi = net_profit / purchase_total if purchase_total > 0 else 0.0
        gate_profit = 10.0 if min_profit is None else max(10.0, min_profit)
        gate_roi = 0.10 if min_roi is None else max(0.10, min_roi)
        if net_profit < gate_profit or roi < gate_roi:
            return EXCLUDED, (
                "Uncertainty is real, but the supported margin is below the level "
                "that justifies manual review"
            )
        if confidence is not None and confidence < 0.40:
            return EXCLUDED, (
                "Uncertainty cannot be resolved by review because the valuation "
                "evidence is too weak"
            )
        if identity_level == "limited" and (
            net_profit < max(40.0, gate_profit * 2)
            or (confidence or 0.0) < 0.50
        ):
            return EXCLUDED, (
                "Only a family-level identity is available; too weak to ask the "
                "user to verify a specific product"
            )
        return NEEDS_VERIFICATION, "Potentially profitable, but a material uncertainty needs checking"
    if risk_level == "high":
        return EXCLUDED, "High risk assessment; not presented as an opportunity"
    if review_resolved:
        return EXCLUDED, "Approved review did not produce profitable, supported evidence"
    return EXCLUDED, "Below the saved profit, ROI or evidence thresholds"
