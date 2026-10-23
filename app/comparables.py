"""Comparable validation.

A product key is only as trustworthy as the listings filed under it. Before a
listing can influence an estimate it has to describe the same *thing* as the
candidate: the same product class, the same model and the same variant. Matching
titles are not enough, because an accessory, a compatible device or a complete
machine can share almost every word with the part it contains.

Every rejected comparable is kept with a reason so the deal drawer can explain
why a price was not used. Validation is deliberately deterministic and cheap:
the pools are large and it runs once per product key per refresh.
"""
from __future__ import annotations

import re
from typing import Any

from .identification import (
    COMPATIBILITY_TAIL_RE,
    MEDIA_CARD_RE,
    subject_phrase,
    system_assembly_evidence,
)
from .variants import (
    INCOMPATIBLE,
    Variant,
    compare_variants,
    variant_from_listing,
    variant_from_title,
)

_PARTS_RE = re.compile(
    r"\b(?:for\s+parts|spares?\s+or\s+repairs?|faulty|not\s+working|broken|"
    r"dead|untested|no\s+power|screen\s+assembly|logic\s+board|motherboard\s+only|"
    r"bare\s+board|chassis\s+only|shell\s+only|frame\s+only|empty\s+box|box\s+only)\b",
    re.I,
)
_CAPACITY_GB_RE = re.compile(r"\b(\d{1,4})\s*gb\b", re.I)
_CAPACITY_TB_RE = re.compile(r"\b(\d(?:\.\d)?)\s*tb\b", re.I)
_COMPONENT_KINDS = {"cpu", "gpu", "ram", "ssd", "motherboard"}
_COMPLETE_SYSTEM_RE = re.compile(
    r"\b(?:desktop\s+(?:pc|computer)|gaming\s+pc|pc\s+tower|tower\s+pc|"
    r"complete\s+(?:pc|system|computer)|workstation\s+pc|all[-\s]in[-\s]one|"
    r"sff\s+(?:pc|desktop)|prodesk|pro\s+desk|optiplex|elitedesk|thinkcentre)\b",
    re.I,
)


def key_capacity(product_key: str) -> int | None:
    """The capacity a product key asserts, in GB, when it asserts one."""
    if not product_key:
        return None
    match = re.search(r"(?:^|:|-)(\d{1,4})gb(?::|$)", product_key)
    if match:
        return int(match.group(1))
    match = re.search(r"(?:^|:|-)(\d)tb(?::|$)", product_key)
    if match:
        return int(match.group(1)) * 1024
    return None


def title_capacities(title: str) -> set[int]:
    values = {int(match.group(1)) for match in _CAPACITY_GB_RE.finditer(title or "")}
    values |= {int(float(match.group(1)) * 1024) for match in _CAPACITY_TB_RE.finditer(title or "")}
    return values


def comparable_rejection(
    comp: dict[str, Any],
    *,
    kind: str,
    product_key: str,
) -> str | None:
    """Return the reason a comparable cannot be used, or ``None``.

    The checks are product-class tests, not a list of bad sellers or models:
    storage media is never a console, a complete machine is never a loose
    processor, a part is never a working device, and a stated capacity must
    agree with the product key.
    """
    title = str(comp.get("title") or "")
    if not title:
        return "untitled observation"
    source = str(comp.get("source") or comp.get("marketplace") or "")
    if source == "cex":
        # CeX rows are a retail reference, not a marketplace comparable.
        return None
    if comp.get("verified") and comp.get("is_sold"):
        # Completed sales are still identity-checked below, but their condition
        # wording is a historical record rather than an asking description.
        pass
    subject = subject_phrase(title)
    lowered = subject.lower()

    if _PARTS_RE.search(title) and not comp.get("is_sold"):
        return "faulty, parts-only or packaging listing"

    # Titles that mention an accessory, storage media or a game are checked by
    # the authoritative classifier instead of another keyword rule. This is the
    # only expensive path, so it runs rarely and settles the ambiguous cases:
    # "Steam Deck with carry case" stays a console, "PS5 console faceplates"
    # does not.
    suspicious_cue = bool(MEDIA_CARD_RE.search(subject)) or bool(
        re.search(
            r"\b(?:faceplates?|controllers?|gamepads?|kinect|headsets?|carry\s+case|"
            r"travel\s+case|charger|power\s+supply|cable|memory\s+card|sd\s+card|"
            r"cartridge|disc\s+only|game\s+disc|video\s+game|joysticks?|thumbsticks?|"
            r"analog\s+stick|hall\s+effect|backplates?|buttons?|triggers?|grips?)\b",
            subject,
            re.I,
        )
    )
    if suspicious_cue and kind != "other":
        from .identification import identify_listing

        identified = identify_listing(title)
        if identified.product_key != product_key:
            if identified.product_key is None:
                return "accessory, media or bundle rather than the product"
            return (
                f"identifies as {identified.product_name or identified.product_key}, "
                "not this product"
            )

    if kind in _COMPONENT_KINDS:
        if system_assembly_evidence(subject):
            return "complete system, not a standalone component"
        # Bare "desktop" or "SFF" describes a component's form factor. It is
        # not a machine: "Desktop HDD 2TB" and "Ryzen 7 7700 desktop processor"
        # were both being discarded as complete computers, which starved the
        # storage and CPU pools of valid evidence.
        if _COMPLETE_SYSTEM_RE.search(subject):
            return "complete system, not a standalone component"
        if re.search(r"\b(?:laptop|notebook|tablet|phone)\b", subject, re.I):
            return "portable device, not a standalone component"



    expected = key_capacity(product_key)
    if expected:
        stated = title_capacities(title)
        # Only reject when the comparable states a capacity and none of them
        # matches. A listing that omits capacity has weaker evidence, not
        # contradictory evidence.
        if stated and expected not in stated:
            stated_text = ", ".join(f"{value}GB" for value in sorted(stated))
            return f"capacity {stated_text} does not match {expected}GB"

    if (
        COMPATIBILITY_TAIL_RE.search(title)
        and not comp.get("is_sold")
        and re.search(r"\b(?:for|fits?|compatible\s+with)\s+the\s+", lowered)
    ):
        return "compatibility-only mention"
    return None


def _cex_variant(
    row: dict[str, Any], candidate: Variant | None
) -> tuple[str, str, str, str]:
    """CeX quotes are already keyed to the product; only reject a contradictory title."""
    if candidate is None:
        return "exact", "", "", ""
    comp = variant_from_title(str(row.get("title") or ""))
    if not comp.key:
        return "exact", "", "", comp.label
    def without_refurb(value: str | None) -> str:
        text = value or ""
        return text[: -len(":refurbished")] if text.endswith(":refurbished") else text

    if without_refurb(comp.key) != without_refurb(candidate.key):
        match = compare_variants(candidate, comp)
        if match.status == INCOMPATIBLE:
            return match.status, match.reason, match.field, match.comparable_variant
    return "exact", "", "", comp.label


def split_pool(
    rows: list[dict[str, Any]],
    *,
    kind: str,
    product_key: str,
    candidate: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition a comparable pool into usable and rejected rows.

    Product-class checks run first, then the structured variant comparison. A
    conflicting model, generation, display, capacity, VRAM, form factor or
    condition is rejected outright. An unstated attribute is accepted with an
    ``uncertain`` marker so it can never be mistaken for an exact match.
    """
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    candidate_variant = variant_from_listing(candidate) if candidate else None
    for row in rows:
        reason = comparable_rejection(row, kind=kind, product_key=product_key)
        if reason:
            rejected.append(
                {
                    "title": row.get("title"),
                    "price": row.get("price"),
                    "marketplace": row.get("marketplace") or row.get("source"),
                    "evidence": "sold" if row.get("is_sold") else "asking",
                    "reason": reason,
                    "conflict": "capacity" if reason.startswith("capacity") else None,
                    "match_quality": INCOMPATIBLE,
                    "identified_variant": None,
                }
            )
            continue
        quality = "exact"
        match_reason = ""
        conflict = ""
        identified_variant = None
        if candidate_variant is not None:
            source = str(row.get("source") or row.get("marketplace") or "").lower()
            if source == "cex":
                quality, match_reason, conflict, identified_variant = _cex_variant(
                    row, candidate_variant
                )
            else:
                comp_variant = variant_from_title(str(row.get("title") or ""))
                if (
                    comp_variant.key is None
                    and row.get("is_sold")
                    and row.get("product_key") == product_key
                ):
                    # A completed sale explicitly filed under this product key
                    # is keyed evidence, not a title-similarity guess. The key
                    # itself is audited separately; its title may be terse.
                    quality = "exact"
                    match_reason = "completed sale explicitly keyed to this product"
                    conflict = ""
                    identified_variant = candidate_variant.label
                else:
                    match = compare_variants(candidate_variant, comp_variant)
                    quality = match.status
                    match_reason = match.reason
                    conflict = match.field
                    identified_variant = match.comparable_variant
            if quality == INCOMPATIBLE:
                rejected.append(
                    {
                        "title": row.get("title"),
                        "price": row.get("price"),
                        "marketplace": row.get("marketplace") or row.get("source"),
                        "evidence": "sold" if row.get("is_sold") else "asking",
                        "reason": match_reason or "incompatible product variant",
                        "conflict": conflict,
                        "match_quality": quality,
                        "identified_variant": identified_variant,
                    }
                )
                continue
        accepted.append(
            {
                **row,
                "match_quality": quality,
                "match_reason": match_reason,
                "variant_conflict": conflict or None,
                "identified_variant": identified_variant,
            }
        )
    return accepted, rejected
