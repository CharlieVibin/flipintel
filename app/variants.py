"""Structured product-variant compatibility.

A product key is a grouping label, not proof that two listings describe the same
hardware. This module re-reads the title of a comparable, derives the attributes
that actually change its value, and compares them with the candidate. A
conflicting critical attribute is an outright rejection regardless of title
similarity, seller reputation or price. An unstated attribute is uncertain, not
a match.

The rules are organised by product kind so a storage capacity is critical for a
handset but not for a console whose only factory capacity exists. Steam Deck,
GPU, console, phone/tablet, memory and drive variants each have their own
comparison because the attributes that matter differ by category.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

EXACT = "exact"
UNCERTAIN = "uncertain"
INCOMPATIBLE = "incompatible"

_PARTS_RE = re.compile(
    r"\b(?:for\s+parts|spares?\s+or\s+repairs?|faulty|not\s+working|broken|"
    r"dead|untested|no\s+power|screen\s+assembly|logic\s+board|motherboard\s+only|"
    r"bare\s+board|chassis\s+only|shell\s+only|frame\s+only|empty\s+box|box\s+only)\b",
    re.I,
)
_REFURB_RE = re.compile(
    r"\b(?:refurbished|refurb|renewed|reconditioned|seller\s+refurbished)\b",
    re.I,
)
_OLED_RE = re.compile(r"\boled\b", re.I)
_LCD_RE = re.compile(r"\b(?:lcd|non[- ]?oled|not\s+oled|original\s+display)\b", re.I)
_CAPACITY_RE = re.compile(r"\b(\d{1,4})\s*(gb|tb)\b", re.I)
_UPGRADE_RE = re.compile(r"\b(?:upgraded?|upgrade)\b", re.I)
_UPGRADE_TO_RE = re.compile(
    r"\b(?:upgraded?|upgrade)\s*(?:to|with)?\s*(?:a\s*)?(\d{1,4})\s*(gb|tb)\b",
    re.I,
)
_UPGRADE_FROM_TO_RE = re.compile(
    r"\b(\d{1,4})\s*(gb|tb)\s*(?:ssd|nvme|storage)?\s*(?:upgraded?|upgrade)\s*"
    r"(?:to|with)\s*(\d{1,4})\s*(gb|tb)\b",
    re.I,
)


def _json_attrs(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@lru_cache(maxsize=8192)
def _identify(title: str):
    from .identification import identify_listing

    return identify_listing(title or "")


@dataclass(frozen=True)
class Variant:
    key: str | None
    kind: str
    attrs: dict[str, Any] = field(default_factory=dict)
    name: str = ""
    title: str = ""
    is_faulty: bool = False
    is_refurbished: bool = False

    @property
    def label(self) -> str:
        return self.name or self.key or "unrecognised variant"


@dataclass(frozen=True)
class VariantMatch:
    status: str
    reason: str = ""
    field: str = ""
    candidate_variant: str = ""
    comparable_variant: str = ""


def variant_from_listing(listing: dict[str, Any]) -> Variant:
    """The candidate's *stored* identity, enriched by its title.

    Stored attrs are authoritative for the listing being valued. The title is
    parsed as well so a newly-added attribute is visible before the audit has
    rewritten the row.
    """
    title = str(listing.get("title") or "")
    stored = _json_attrs(listing.get("attrs") or listing.get("attrs_json"))
    identified = _identify(title) if title else None
    merged: dict[str, Any] = dict(identified.attrs or {}) if identified else {}
    for key, value in stored.items():
        if value is not None:
            merged[key] = value
    key = str(listing.get("product_key") or "").strip() or None
    if not key and identified:
        key = identified.product_key
    kind = str(listing.get("listing_kind") or listing.get("kind") or "").strip()
    if not kind and identified:
        kind = identified.kind
    name = str(listing.get("product_name") or "").strip()
    if not name and identified:
        name = identified.product_name or ""
    refurbished = bool(merged.get("refurbished")) or bool(
        key and key.endswith(":refurbished")
    ) or bool(_REFURB_RE.search(title))
    return Variant(
        key=key,
        kind=kind or "other",
        attrs=merged,
        name=name or (identified.product_name if identified else "") or "",
        title=title,
        is_faulty=bool(identified.is_faulty) if identified else False,
        is_refurbished=refurbished,
    )


def variant_from_title(title: str, *, key: str | None = None) -> Variant:
    """Derive the variant a comparable title actually describes."""
    identified = _identify(title or "")
    attrs = dict(identified.attrs or {})
    resolved_key = identified.product_key or key
    refined = bool(attrs.get("refurbished")) or bool(
        resolved_key and resolved_key.endswith(":refurbished")
    ) or bool(_REFURB_RE.search(title or ""))
    return Variant(
        key=resolved_key,
        kind=identified.kind or "other",
        attrs=attrs,
        name=identified.product_name or "",
        title=title or "",
        is_faulty=bool(identified.is_faulty),
        is_refurbished=refined,
    )


def _capacity(text: str) -> int | None:
    for match in _CAPACITY_RE.finditer(text or ""):
        value = int(match.group(1))
        unit = match.group(2).lower()
        gb = value * 1024 if unit == "tb" else value
        if 8 <= gb <= 8192:
            return gb
    return None


def _key_capacity(key: str | None) -> int | None:
    if not key:
        return None
    match = re.search(r"(?:^|:|-)(\d{1,4})gb(?::|$)", key)
    if match:
        return int(match.group(1))
    match = re.search(r"(?:^|:|-)(\d{1,2})tb(?::|$)", key)
    if match:
        return int(match.group(1)) * 1024
    return None


def _norm_suffix(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z]+", "-", text).strip("-")
    if text == "tisuper":
        return "ti-super"
    return text


def _is_steam_deck(variant: Variant) -> bool:
    return bool(
        (variant.key and variant.key.startswith("console:steam-deck"))
        or re.search(r"\bsteam\s+deck\b", variant.title, re.I)
        or str(variant.attrs.get("family") or "") == "steam-deck"
    )


def _steam_display(variant: Variant) -> str | None:
    display = str(variant.attrs.get("display") or "").lower()
    if display in {"oled", "lcd"}:
        return display
    key = variant.key or ""
    if key.startswith("console:steam-deck-oled"):
        return "oled"
    if key.startswith("console:steam-deck-lcd"):
        return "lcd"
    title = variant.title
    if _OLED_RE.search(title):
        return "oled"
    if _LCD_RE.search(title):
        return "lcd"
    storage = _steam_storage(variant)
    # Valve never sold a 64GB or 256GB OLED Steam Deck.
    if storage in {64, 256}:
        return "lcd"
    return None


def _steam_storage(variant: Variant) -> int | None:
    value = variant.attrs.get("storage_gb")
    try:
        if value:
            return int(value)
    except (TypeError, ValueError):
        pass
    return _key_capacity(variant.key) or _capacity(variant.title)


def _steam_origin(variant: Variant) -> str | None:
    origin = str(variant.attrs.get("storage_origin") or "").lower()
    if origin in {"upgraded", "factory"}:
        return origin
    if variant.key and variant.key.endswith(":upgraded"):
        return "upgraded"
    if _UPGRADE_RE.search(variant.title):
        return "upgraded"
    display = _steam_display(variant)
    storage = _steam_storage(variant)
    if display == "lcd" and storage and storage >= 1024:
        # The largest factory LCD Steam Deck was 512GB.
        return "upgraded"
    if storage in {64, 256, 512, 1024} and display:
        return "factory"
    return None


def _compare_steam_deck(candidate: Variant, comp: Variant) -> tuple[str, str, str]:
    status = EXACT
    reason = ""
    field = ""
    c_display, k_display = _steam_display(candidate), _steam_display(comp)
    if c_display and k_display and c_display != k_display:
        return (
            INCOMPATIBLE,
            f"conflicting display technology: {c_display.upper()} versus {k_display.upper()}",
            "display",
        )
    if (c_display is None) != (k_display is None):
        status, reason, field = UNCERTAIN, "display technology is not stated", "display"
    c_storage, k_storage = _steam_storage(candidate), _steam_storage(comp)
    if c_storage and k_storage and c_storage != k_storage:
        return (
            INCOMPATIBLE,
            f"conflicting storage capacity: {c_storage}GB versus {k_storage}GB",
            "storage",
        )
    if (c_storage is None) != (k_storage is None):
        status, reason, field = UNCERTAIN, "storage capacity is not stated", "storage"
    c_origin, k_origin = _steam_origin(candidate), _steam_origin(comp)
    if c_origin and k_origin and c_origin != k_origin:
        return (
            INCOMPATIBLE,
            f"conflicting storage origin: {c_origin} versus {k_origin}",
            "storage_origin",
        )
    if (c_origin is None) != (k_origin is None):
        status, reason, field = UNCERTAIN, "storage origin is not stated", "storage_origin"
    return status, reason, field


# Models whose retail SKU has exactly one VRAM capacity can be inferred when
# the seller omits it. Models with several factory capacities (RTX 3060 8/12GB,
# RTX 4060 Ti 8/16GB, RTX 3080 10/12GB) stay unknown so they cannot be mixed.
_GPU_VRAM_OPTIONS: dict[tuple[str, str], set[int]] = {
    ("4060", ""): {8},
    ("4060", "ti"): {8, 16},
    ("4070", ""): {12},
    ("4070", "super"): {12},
    ("4070", "ti"): {12},
    ("4070", "ti-super"): {16},
    ("4080", ""): {16},
    ("4080", "super"): {16},
    ("4090", ""): {24},
    ("3060", ""): {8, 12},
    ("3060", "ti"): {8},
    ("3070", ""): {8},
    ("3070", "ti"): {8},
    ("3080", ""): {10, 12},
    ("3080", "ti"): {12},
    ("3090", ""): {24},
    ("5070", ""): {12},
    ("5070", "ti"): {16},
    ("5080", ""): {16},
    ("5090", ""): {32},
    ("7600", ""): {8},
    ("7600", "xt"): {16},
    ("7700", "xt"): {12},
    ("7800", "xt"): {16},
    ("7900", "xt"): {20},
    ("7900", "xtx"): {24},
}


def _gpu_fields(variant: Variant) -> dict[str, Any]:
    attrs = variant.attrs
    model = attrs.get("model")
    suffix = _norm_suffix(attrs.get("suffix"))
    vram = attrs.get("vram_gb")
    form = str(attrs.get("form_factor") or "").lower()
    if not form:
        if variant.key and variant.key.endswith(":laptop"):
            form = "laptop"
        elif re.search(r"\b(?:laptop|notebook|mobile|max-q)\b", variant.title, re.I):
            form = "laptop"
        else:
            form = "desktop"
    if model is None and variant.key:
        match = re.search(r"gpu:(?:rtx|gtx|rx|arc|gt)-?([a-z]*)-?(\d{3,4})", variant.key)
        if match:
            model = match.group(2)
    if vram is None and variant.key:
        # Product keys are derived from the listing but persisted as the
        # explicit identity, so a VRAM capacity encoded in the key is evidence.
        match = re.search(r"gpu:.*?-(\d{1,3})gb(?:$|:)", variant.key)
        if match:
            vram = match.group(1)
    try:
        vram = int(vram) if vram else None
    except (TypeError, ValueError):
        vram = None
    if vram is None and model:
        options = _GPU_VRAM_OPTIONS.get((str(model), suffix))
        if options and len(options) == 1:
            vram = next(iter(options))
    return {
        "brand": str(attrs.get("brand") or "").lower() or None,
        "model": str(model) if model is not None else None,
        "suffix": suffix,
        "vram": vram,
        "form": form or None,
    }


def _compare_gpu(candidate: Variant, comp: Variant) -> tuple[str, str, str]:
    c, k = _gpu_fields(candidate), _gpu_fields(comp)
    if c["brand"] and k["brand"] and c["brand"] != k["brand"]:
        return INCOMPATIBLE, f"conflicting GPU brand: {c['brand']} versus {k['brand']}", "brand"
    if c["model"] and k["model"] and c["model"] != k["model"]:
        return INCOMPATIBLE, f"conflicting GPU model: {c['model']} versus {k['model']}", "model"
    if c["model"] is None or k["model"] is None:
        return UNCERTAIN, "GPU model is not stated", "model"
    if c["suffix"] != k["suffix"]:
        return (
            INCOMPATIBLE,
            f"conflicting GPU variant: {c['suffix'] or 'base'} versus {k['suffix'] or 'base'}",
            "suffix",
        )
    if c["vram"] and k["vram"] and c["vram"] != k["vram"]:
        return (
            INCOMPATIBLE,
            f"conflicting VRAM: {c['vram']}GB versus {k['vram']}GB",
            "vram",
        )
    if (c["vram"] is None) != (k["vram"] is None):
        return UNCERTAIN, "VRAM capacity is not stated", "vram"
    if c["form"] and k["form"] and c["form"] != k["form"]:
        return (
            INCOMPATIBLE,
            f"conflicting form factor: {c['form']} versus {k['form']}",
            "form_factor",
        )
    return EXACT, "", ""


def _compare_cpu(candidate: Variant, comp: Variant) -> tuple[str, str, str]:
    def fields(v: Variant) -> tuple[str | None, str, str | None]:
        attrs = v.attrs
        model = str(attrs.get("model") or "") or None
        suffix = _norm_suffix(attrs.get("suffix"))
        brand = str(attrs.get("brand") or "").lower() or None
        return model, suffix, brand

    c_model, c_suffix, c_brand = fields(candidate)
    k_model, k_suffix, k_brand = fields(comp)
    if c_brand and k_brand and c_brand != k_brand:
        return INCOMPATIBLE, "conflicting CPU brand", "brand"
    if c_model and k_model and c_model != k_model:
        return INCOMPATIBLE, f"conflicting CPU model: {c_model} versus {k_model}", "model"
    if not c_model or not k_model:
        return UNCERTAIN, "CPU model is not stated", "model"
    if c_suffix != k_suffix:
        return (
            INCOMPATIBLE,
            f"conflicting CPU suffix: {c_suffix or 'base'} versus {k_suffix or 'base'}",
            "suffix",
        )
    return EXACT, "", ""


def _strip_storage(key: str | None) -> str | None:
    if not key:
        return None
    value = key[: -len(":refurbished")] if key.endswith(":refurbished") else key
    value = re.sub(r"-(?:\d{2,4})gb$", "", value)
    value = re.sub(r"-(?:1|2|4|8)tb$", "", value)
    return value


def _compare_phone_tablet(candidate: Variant, comp: Variant) -> tuple[str, str, str]:
    c_base, k_base = _strip_storage(candidate.key), _strip_storage(comp.key)
    if c_base and k_base and c_base != k_base:
        return (
            INCOMPATIBLE,
            f"different model/variant: {candidate.label} versus {comp.label}",
            "model",
        )
    if not c_base or not k_base:
        return UNCERTAIN, "exact model is not stated", "model"
    c_storage = candidate.attrs.get("storage_gb") or _key_capacity(candidate.key)
    k_storage = comp.attrs.get("storage_gb") or _key_capacity(comp.key)
    try:
        c_storage = int(c_storage) if c_storage else None
    except (TypeError, ValueError):
        c_storage = None
    try:
        k_storage = int(k_storage) if k_storage else None
    except (TypeError, ValueError):
        k_storage = None
    if c_storage and k_storage and c_storage != k_storage:
        return (
            INCOMPATIBLE,
            f"conflicting storage capacity: {c_storage}GB versus {k_storage}GB",
            "storage",
        )
    if (c_storage is None) != (k_storage is None):
        return UNCERTAIN, "storage capacity is not stated", "storage"
    # Carrier lock is only critical when both titles state a different state.
    c_lock = candidate.attrs.get("network_lock")
    k_lock = comp.attrs.get("network_lock")
    if c_lock and k_lock and c_lock != k_lock:
        return INCOMPATIBLE, f"conflicting network lock: {c_lock} versus {k_lock}", "network_lock"
    return EXACT, "", ""


def _ram_fields(variant: Variant) -> dict[str, Any]:
    attrs = variant.attrs
    def integer(name: str) -> int | None:
        value = attrs.get(name)
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    key = variant.key or ""
    form = str(attrs.get("form_factor") or "") or None
    ecc = attrs.get("ecc")
    if ecc is None:
        # Older rows encoded ECC in the product key or used "ecc" as a form
        # factor. Both mean the same thing and must not be compared as a
        # laptop/desktop form mismatch.
        ecc = key.endswith(":ecc") or form == "ecc"
        if form == "ecc":
            form = None
    return {
        "ddr": str(attrs.get("ddr") or "") or None,
        "capacity": integer("capacity_gb") or _key_capacity(key),
        "speed": integer("speed_mhz"),
        "kit_count": integer("kit_count"),
        "kit_size": integer("kit_size_gb"),
        "form": form,
        "ecc": bool(ecc),
    }


def _compare_ram(candidate: Variant, comp: Variant) -> tuple[str, str, str]:
    c, k = _ram_fields(candidate), _ram_fields(comp)
    # ECC and non-ECC modules are electrically different products. Check this
    # before the physical form factor, because both can be DIMMs or SO-DIMMs.
    if c["ecc"] != k["ecc"]:
        return (
            INCOMPATIBLE,
            "conflicting ECC support: "
            f"{'ECC' if c['ecc'] else 'non-ECC'} versus "
            f"{'ECC' if k['ecc'] else 'non-ECC'}",
            "ecc",
        )
    for field_name, label in (
        ("ddr", "memory generation"),
        ("capacity", "total capacity"),
        ("form", "module form factor"),
    ):
        if c[field_name] and k[field_name] and c[field_name] != k[field_name]:
            return (
                INCOMPATIBLE,
                f"conflicting {label}: {c[field_name]} versus {k[field_name]}",
                field_name,
            )
        if (c[field_name] is None) != (k[field_name] is None):
            return UNCERTAIN, f"{label} is not stated", field_name
    if c["kit_count"] and k["kit_count"] and c["kit_size"] and k["kit_size"]:
        if (c["kit_count"], c["kit_size"]) != (k["kit_count"], k["kit_size"]):
            return (
                INCOMPATIBLE,
                "conflicting kit arrangement: "
                f"{c['kit_count']}x{c['kit_size']}GB versus {k['kit_count']}x{k['kit_size']}GB",
                "kit",
            )
    elif (c["kit_count"] is None) != (k["kit_count"] is None):
        return UNCERTAIN, "kit arrangement is not stated", "kit"
    if c["speed"] and k["speed"] and c["speed"] != k["speed"]:
        return (
            INCOMPATIBLE,
            f"conflicting memory speed: {c['speed']}MHz versus {k['speed']}MHz",
            "speed",
        )
    if (c["speed"] is None) != (k["speed"] is None):
        return UNCERTAIN, "memory speed is not stated", "speed"
    return EXACT, "", ""


def _compare_storage(candidate: Variant, comp: Variant) -> tuple[str, str, str]:
    def fields(v: Variant) -> dict[str, Any]:
        attrs = v.attrs
        return {
            "capacity": attrs.get("capacity_gb") or _key_capacity(v.key),
            "interface": str(attrs.get("interface") or "").lower() or None,
            "form": str(attrs.get("form_factor") or "").lower() or None,
        }

    c, k = fields(candidate), fields(comp)
    if c["capacity"] and k["capacity"] and c["capacity"] != k["capacity"]:
        return (
            INCOMPATIBLE,
            f"conflicting drive capacity: {c['capacity']}GB versus {k['capacity']}GB",
            "capacity",
        )
    if (c["capacity"] is None) != (k["capacity"] is None):
        return UNCERTAIN, "drive capacity is not stated", "capacity"
    if c["interface"] and k["interface"] and c["interface"] != k["interface"]:
        return (
            INCOMPATIBLE,
            f"conflicting drive interface: {c['interface']} versus {k['interface']}",
            "interface",
        )
    if (c["interface"] is None) != (k["interface"] is None):
        return UNCERTAIN, "drive interface is not stated", "interface"
    if c["form"] and k["form"] and c["form"] != k["form"]:
        return (
            INCOMPATIBLE,
            f"conflicting drive form factor: {c['form']} versus {k['form']}",
            "form_factor",
        )
    if (c["form"] is None) != (k["form"] is None):
        return UNCERTAIN, "drive form factor is not stated", "form_factor"
    return EXACT, "", ""


def _console_parts(key: str | None) -> dict[str, Any]:
    value = (key or "")[: -len(":refurbished")] if (key or "").endswith(":refurbished") else (key or "")
    if value.startswith("console:ps5"):
        return {
            "family": "playstation",
            "model": "ps5",
            "revision": "pro" if "-pro" in value else "slim" if "-slim" in value else "base",
            "edition": "digital" if "digital" in value else "disc" if "disc" in value else None,
        }
    if value.startswith("console:ps4"):
        return {
            "family": "playstation",
            "model": "ps4",
            "revision": "pro" if "-pro" in value else "slim" if "-slim" in value else "base",
        }
    if value.startswith("console:xbox-series-x"):
        return {"family": "xbox", "model": "series-x"}
    if value.startswith("console:xbox-series-s"):
        return {"family": "xbox", "model": "series-s"}
    if value.startswith("console:xbox-one-x"):
        return {"family": "xbox", "model": "one-x"}
    if value.startswith("console:xbox-one-s"):
        return {"family": "xbox", "model": "one-s"}
    if value.startswith("console:xbox-one"):
        return {"family": "xbox", "model": "one"}
    if value.startswith("console:xbox-360-s"):
        return {"family": "xbox", "model": "360-s"}
    if value.startswith("console:xbox-360-elite"):
        return {"family": "xbox", "model": "360-elite"}
    if value.startswith("console:xbox-360"):
        return {"family": "xbox", "model": "360"}
    if value.startswith("console:switch-2"):
        return {"family": "switch", "model": "switch-2", "display": "lcd"}
    if value.startswith("console:switch-lite"):
        return {"family": "switch", "model": "switch-lite", "display": "lcd"}
    if value.startswith("console:switch-oled"):
        return {"family": "switch", "model": "switch", "display": "oled"}
    if value.startswith("console:switch"):
        return {"family": "switch", "model": "switch", "display": "lcd"}
    if value:
        tail = value[len("console:") :]
        return {"family": tail.split("-")[0], "model": tail}
    return {}


def _console_capacity(variant: Variant) -> int | None:
    value = variant.attrs.get("storage_gb")
    try:
        value = int(value) if value else None
    except (TypeError, ValueError):
        value = None
    if value:
        return value
    value = _key_capacity(variant.key)
    if value:
        return value
    for match in re.finditer(r"\b(\d{1,4})\s*(gb|tb)\b", variant.title, re.I):
        neighbourhood = variant.title[max(0, match.start() - 18) : match.end() + 18]
        if re.search(r"\b(?:micro\s*sd|microsd|sd\s*card|memory\s*card)\b", neighbourhood, re.I):
            continue
        amount = int(match.group(1))
        return amount * 1024 if match.group(2).lower() == "tb" else amount
    return None


# Only these PlayStation keys have a factory disc/digital edition split.
# The PS5 Pro is digital as standard; a disc drive is an optional accessory,
# not a separate console edition, so an "edition not stated" warning there was
# a false uncertainty.
_CONSOLE_EDITION_KEYS = {
    "console:ps5",
    "console:ps5-digital",
    "console:ps5-slim",
    "console:ps5-slim-digital",
}


def _console_edition(variant: Variant) -> str | None:
    edition = str(variant.attrs.get("edition") or "").lower()
    if edition in {"digital", "disc"}:
        return edition
    key = (variant.key or "").removesuffix(":refurbished")
    if key.endswith("-digital"):
        return "digital"
    if re.search(r"\b(?:digital|disc[- ]?less)\b", variant.title, re.I):
        return "digital"
    if re.search(r"\b(?:disc|blu[- ]?ray)\b", variant.title, re.I):
        return "disc"
    return None


def _compare_console(candidate: Variant, comp: Variant) -> tuple[str, str, str]:
    c, k = _console_parts(candidate.key), _console_parts(comp.key)
    if c and k and c.get("family") != k.get("family"):
        return INCOMPATIBLE, "different console platform", "family"
    if c and k and c.get("model") != k.get("model"):
        return (
            INCOMPATIBLE,
            f"conflicting console model: {c.get('model')} versus {k.get('model')}",
            "model",
        )
    revision_c = c.get("revision") if c else candidate.attrs.get("revision")
    revision_k = k.get("revision") if k else comp.attrs.get("revision")
    if revision_c and revision_k and revision_c != revision_k:
        return (
            INCOMPATIBLE,
            f"conflicting console revision: {revision_c} versus {revision_k}",
            "revision",
        )
    edition_applies = bool(
        (candidate.key or "").removesuffix(":refurbished") in _CONSOLE_EDITION_KEYS
        or (comp.key or "").removesuffix(":refurbished") in _CONSOLE_EDITION_KEYS
    )
    if edition_applies:
        edition_c = _console_edition(candidate)
        edition_k = _console_edition(comp)
        if edition_c and edition_k and edition_c != edition_k:
            return (
                INCOMPATIBLE,
                f"conflicting console edition: {edition_c} versus {edition_k}",
                "edition",
            )
        if (edition_c is None) != (edition_k is None):
            return UNCERTAIN, "console edition is not stated", "edition"
    display_c = candidate.attrs.get("display") or (c.get("display") if c else None)
    display_k = comp.attrs.get("display") or (k.get("display") if k else None)
    if display_c and display_k and display_c != display_k:
        return (
            INCOMPATIBLE,
            f"conflicting display: {display_c} versus {display_k}",
            "display",
        )
    if (display_c is None) != (display_k is None):
        return UNCERTAIN, "console display is not stated", "display"
    storage_c, storage_k = _console_capacity(candidate), _console_capacity(comp)
    if storage_c and storage_k and storage_c != storage_k:
        return (
            INCOMPATIBLE,
            f"conflicting console storage: {storage_c}GB versus {storage_k}GB",
            "storage",
        )
    return EXACT, "", ""


def compare_variants(candidate: Variant, comp: Variant) -> VariantMatch:
    """Compare the candidate with one comparable and return a verdict."""
    names = dict(
        candidate_variant=candidate.label,
        comparable_variant=comp.label,
    )
    if comp.is_faulty and not candidate.is_faulty:
        return VariantMatch(
            INCOMPATIBLE, "faulty or parts item cannot price working hardware", "condition", **names
        )
    if comp.is_refurbished and not candidate.is_refurbished:
        return VariantMatch(
            INCOMPATIBLE,
            "refurbished stock is a different market from an ordinary used item",
            "condition",
            **names,
        )
    if candidate.is_refurbished and not comp.is_refurbished:
        return VariantMatch(
            UNCERTAIN,
            "candidate is refurbished but the comparable is not",
            "condition",
            **names,
        )
    if (candidate.kind and comp.kind and candidate.kind != comp.kind):
        return VariantMatch(
            INCOMPATIBLE,
            f"different product class: {candidate.kind} versus {comp.kind}",
            "kind",
            **names,
        )
    if _is_steam_deck(candidate) or _is_steam_deck(comp):
        # The title itself can prove LCD versus OLED even when the classifier
        # refused an exact key because the listing mentions accessories.
        status, reason, field = _compare_steam_deck(candidate, comp)
    elif not candidate.key or not comp.key:
        return VariantMatch(
            UNCERTAIN, "the comparable's exact variant is not stated", "model", **names
        )
    elif candidate.kind == "gpu":
        status, reason, field = _compare_gpu(candidate, comp)
    elif candidate.kind == "cpu":
        status, reason, field = _compare_cpu(candidate, comp)
    elif candidate.kind in {"phone", "tablet"}:
        status, reason, field = _compare_phone_tablet(candidate, comp)
    elif candidate.kind == "ram":
        status, reason, field = _compare_ram(candidate, comp)
    elif candidate.kind in {"ssd", "hdd"} or str(candidate.kind).startswith("ssd"):
        status, reason, field = _compare_storage(candidate, comp)
    elif candidate.kind == "console":
        status, reason, field = _compare_console(candidate, comp)
    else:
        status = EXACT if candidate.key == comp.key else UNCERTAIN
        reason = "" if status == EXACT else "exact variant could not be confirmed"
        field = "" if status == EXACT else "model"
    # Category comparisons above are authoritative. Two keys can differ only
    # because one title omitted a non-critical capacity (gpu:rtx-4070-
    # 12gb versus gpu:rtx-4070); once every critical attribute agrees, that is
    # an exact match, not an uncertain one.
    return VariantMatch(status, reason, field, **names)
