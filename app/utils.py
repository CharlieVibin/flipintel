"""Small shared helpers: UTC timestamps, money, text normalisation."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_GBP_RE = re.compile(r"(?:£|gbp\s?)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def hours_ago(hours: float) -> str:
    return iso(utcnow() - timedelta(hours=hours))


def age_hours(value: str | None) -> float | None:
    dt = parse_iso(value)
    if not dt:
        return None
    return max(0.0, (utcnow() - dt).total_seconds() / 3600.0)


def normalise_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).replace("\u00a0", " ")
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    return _WS_RE.sub(" ", text).strip()


def lower_text(value: str | None) -> str:
    return normalise_text(value).lower()


def tokenise(value: str | None) -> list[str]:
    return [t for t in _NON_ALNUM_RE.split(lower_text(value)) if t]


def token_set(value: str | None) -> set[str]:
    return set(tokenise(value))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def parse_money(value: Any) -> float | None:
    """Parse '£1,234.50', '1,234.50 GBP' or a numeric value."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        amount = float(value)
        return round(amount, 2) if math.isfinite(amount) and amount >= 0 else None
    text = normalise_text(str(value))
    if not text:
        return None
    match = _GBP_RE.search(text)
    if match:
        raw = match.group(1)
    else:
        match = re.search(r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)", text)
        if not match:
            return None
        raw = match.group(1)
    try:
        amount = float(raw.replace(",", ""))
    except ValueError:
        return None
    return round(amount, 2) if math.isfinite(amount) and amount >= 0 else None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def json_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def median(values: Iterable[float]) -> float | None:
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def weighted_median(pairs: Iterable[tuple[float, float]]) -> float | None:
    """Median of (value, weight) pairs, ignoring non-finite/non-positive values."""
    rows = sorted((v, w) for v, w in pairs if v > 0 and w > 0 and math.isfinite(v))
    if not rows:
        return None
    total = sum(w for _, w in rows)
    if total <= 0:
        return None
    half = total / 2.0
    cumulative = 0.0
    for value, weight in rows:
        cumulative += weight
        if cumulative >= half:
            return value
    return rows[-1][0]


def weighted_quantile(pairs: Iterable[tuple[float, float]], q: float) -> float | None:
    rows = sorted((v, w) for v, w in pairs if v > 0 and w > 0 and math.isfinite(v))
    if not rows:
        return None
    total = sum(w for _, w in rows)
    if total <= 0:
        return None
    target = clamp(q, 0.0, 1.0) * total
    cumulative = 0.0
    for value, weight in rows:
        cumulative += weight
        if cumulative >= target:
            return value
    return rows[-1][0]


def quantile(values: Iterable[float], q: float) -> float | None:
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    q = clamp(q, 0.0, 1.0)
    pos = (len(vals) - 1) * q
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return vals[low]
    return vals[low] + (vals[high] - vals[low]) * (pos - low)


def money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"£{value:,.2f}"


def slug(value: str) -> str:
    return _NON_ALNUM_RE.sub("-", value.lower()).strip("-")
