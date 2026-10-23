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
    DEVICE_CHASSIS_RE,
    MEDIA_CARD_RE,
    subject_phrase,
    system_assembly_evidence,
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
            r"cartridge|disc\s+only|game\s+disc|video\s+game)\b",
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
        if DEVICE_CHASSIS_RE.search(subject):
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


def split_pool(
    rows: list[dict[str, Any]], *, kind: str, product_key: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition a comparable pool into usable and rejected rows."""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
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
                }
            )
            continue
        accepted.append(row)
    return accepted, rejected
