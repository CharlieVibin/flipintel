"""CeX UK retail and trade-in prices.

CeX publishes its own storefront prices through the search index used by
uk.webuy.com (``search.webuy.io``). That index answers an unauthenticated
request with the same rows the website renders, including retail price, cash
offer, voucher offer, grade, stock and the last price move. It is not an
official API and has no contract; every failure is reported as a failure and is
never converted into a price.

The matching and grade logic is adapted from the MIT-licensed
``cex-uk-prices`` project by Christian Asare-Anderson. The full notice is in
``THIRD_PARTY_NOTICES.md``; the temporary reference copy is not imported.

CeX retail is a **reference**, not a completed sale. It is stored separately
from sold evidence and is discounted for private resale in the valuation.
"""
from __future__ import annotations

import asyncio
import math
import random
import re
import statistics
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from .config import settings
from .db import Database
from .utils import iso, parse_iso, utcnow

SEARCH_URL = "https://search.webuy.io/1/indexes/*/queries"
INDEX = "prod_cex_uk"
ORIGIN = "UK"
REUSE_HOURS = 24 * 7
MIN_INTERVAL = 1.0
HITS_PER_PAGE = 12

# A measured threshold from cex-uk-prices: genuine matches scored 0.62–0.86,
# every wrong product topped out at 0.50. 0.6 sits in that gap.
MIN_MATCH = 0.6
# Distinct products (for example the disc and digital PS5 Slim) whose retail
# prices differ by more than this are ambiguous for a model-level product key.
AMBIGUOUS_SPREAD = 0.12

_NOISE = {
    "the", "and", "for", "with", "new", "used", "genuine", "original", "free",
    "fast", "same", "day", "dispatch", "uk", "gb", "official", "boxed", "set",
    "compatible", "scientific", "quality", "great", "excellent", "condition",
    "unit", "item", "only", "inc", "vat", "postage", "delivery", "sale",
}
_MODELISH = re.compile(r"^(?=.*\d)[a-z0-9-]{2,}$|^(ti|xl|se|xs|pro|max|mini|plus)$")
_VARIANT_TOKENS = {
    "ti", "super", "pro", "max", "plus", "mini", "ultra", "lite", "slim",
    "digital", "disc", "oled", "se", "xl", "x", "s",
}
_CAPACITY = re.compile(r"^\d+(?:gb|tb)$")
_GRADE = re.compile(r"[,\s]\s*([ABC])\s*$", re.I)


def _tokens(text: str) -> set[str]:
    """Lower-cased words, keeping single letters.

    Dropping one-character tokens conflated "Xbox Series X" with "Xbox Series
    S" and "iPhone X" with a bare iPhone, so both products scored a perfect
    match against each other.
    """
    return {
        word.strip(".,()[]/-").lower()
        for word in (text or "").split()
        if word.strip(".,()[]/-")
    }


def _variants(text: str) -> set[str]:
    return {token for token in _tokens(text) if token in _VARIANT_TOKENS}


def _capacities(text: str) -> set[str]:
    return {token for token in _tokens(text) if _CAPACITY.match(token)}


ACCESSORY_WORDS = {
    "stand", "controller", "cable", "charger", "headset", "case", "cover",
    "skin", "faceplate", "faceplates", "game", "games", "manual", "spares",
    "parts", "replacement", "keyboard", "mouse", "stylus", "remote", "mount",
    "bracket",
}


# Presentation-only words. Two rows that differ by nothing but these carry the
# same physical product at different CeX prices (boxed/unboxed/discounted, a
# colour, an included controller colour). They are one model, so their retail
# prices are pooled instead of being treated as rival products.
GENERIC_PRESENTATION = {
    "console", "boxed", "unboxed", "discounted", "discount", "white", "black",
    "silver", "grey", "gray", "gold", "red", "blue", "green", "pink",
    "purple", "neon", "robot", "carbon", "mixed", "joy", "con", "joy-con",
    "controller", "controllers", "wireless", "no", "game", "games", "dlc",
    "gb", "tb", "wifi", "bluetooth",
}
# A cohort whose own prices are this dispersed is not one product.
MAX_COHORT_DISPERSION = 0.25


def _accessory_row(query: str, name: str, category: str) -> bool:
    lowered = f" {name.lower()} "
    query_tokens = _tokens(query)
    for word in ACCESSORY_WORDS:
        if f" {word} " in lowered and word not in query_tokens:
            return True
    return "accessor" in (category or "").lower()


def _specificity(query: str, title: str) -> int:
    """How many distinctive words a CeX row adds beyond the query.

    Zero means the row is the plain product (its extra words are only packaging,
    colour, capacity or the word "console"). A special edition, a digital-only
    model or a different capacity adds words and scores higher, so it cannot win
    the quote on a tie.
    """
    wanted = _tokens(query) - _NOISE
    extras = _tokens(title) - wanted - GENERIC_PRESENTATION
    extras = {word for word in extras if not _CAPACITY.match(word)}
    return len(extras)


def match_score(query: str, title: str) -> float:
    """Fraction of the query's meaningful words carried by the title.

    Every model-ish token is a veto, so "RTX 4060 Ti" can never match a plain
    "RTX 4060" and a 128GB phone cannot match a 256GB row.
    """
    wanted = _tokens(query) - _NOISE
    if not wanted:
        return 0.0
    offered = _tokens(title)
    required = {word for word in wanted if _MODELISH.match(word)}
    if required and not required.issubset(offered):
        return 0.0
    # A candidate with extra variant tokens is a different product (4060 Ti
    # vs 4060, Slim vs Pro). Capacity must match when the query states it;
    # otherwise the row's capacity is retained and used for filtering.
    if _variants(title) != _variants(query):
        return 0.0
    query_capacity = _capacities(query)
    if query_capacity and query_capacity != _capacities(title):
        return 0.0
    return len(wanted & offered) / len(wanted)


@dataclass(frozen=True)
class CexRow:
    name: str
    grade: str
    sell_gbp: float
    cash_gbp: float
    voucher_gbp: float
    cash_pct: int
    voucher_pct: int
    box_id: str
    product_id: str
    category: str
    super_category: str
    stores: tuple[str, ...]
    online_qty: int
    previous_gbp: float
    price_changed: str
    discontinued: bool
    origin: str


def _row_from_hit(hit: dict[str, Any]) -> CexRow | None:
    name = str(hit.get("boxName") or "").strip()
    if not name:
        return None
    grade_match = _GRADE.search(name)
    grade = grade_match.group(1).upper() if grade_match else ""
    clean_name = _GRADE.sub("", name).strip()
    box_id = str(hit.get("boxId") or "")
    product_id = box_id[:-1] if grade and box_id.upper().endswith(grade) else box_id
    return CexRow(
        name=clean_name,
        grade=grade,
        sell_gbp=float(hit.get("sellPrice") or 0),
        cash_gbp=float(hit.get("cashPriceCalculated") or 0),
        voucher_gbp=float(hit.get("exchangePriceCalculated") or 0),
        cash_pct=int(hit.get("buyPerc") or 0),
        voucher_pct=int(hit.get("exchangePerc") or 0),
        box_id=box_id,
        product_id=product_id,
        category=str(hit.get("categoryName") or "").strip(),
        super_category=str(hit.get("superCatName") or "").strip(),
        stores=tuple(
            str(s).strip() for s in (hit.get("stores") or []) if isinstance(s, str)
        ),
        online_qty=int(hit.get("ecomQuantity") or 0),
        previous_gbp=float(hit.get("previousPrice") or 0),
        price_changed=str(hit.get("priceLastChanged") or "")[:10],
        discontinued=bool(hit.get("discontinued")),
        origin=str(hit.get("origin") or "").strip(),
    )


def _preferred_grades(condition: str) -> tuple[str, ...]:
    """CeX grade ladder mapped to our condition vocabulary."""
    condition = (condition or "unknown").lower()
    if condition in {"new", "open_box", "like_new"}:
        return ("A", "B")
    if condition in {"fair", "poor"}:
        return ("C", "B")
    return ("B", "A", "C")


def _search_text(query: str, product_key: str) -> str:
    """A shorter Algolia query. Matching still uses the full model name."""
    drop = {
        "nvidia", "geforce", "amd", "radeon", "apple", "sony", "microsoft",
        "samsung", "google",
    }
    words = [word for word in query.split() if word.lower().strip("()") not in drop]
    if product_key.startswith("gpu:"):
        # VRAM is a filter, not a good search term: asking for "8GB" makes the
        # index return complete desktops with that card instead of the card.
        words = [
            word for word in words
            if not _CAPACITY.match(word.lower().strip("()"))
        ]
    return " ".join(words).strip() or query


def _query_for(product_name: str, product_key: str, attrs: dict[str, Any]) -> str:
    """Build a specific CeX query from the display name plus key attributes."""
    parts = [product_name]
    lowered = product_name.lower()
    if product_key.startswith(("phone:", "tablet:")) and attrs.get("storage_gb"):
        if f"{int(attrs['storage_gb'])}gb" not in lowered.replace(" ", ""):
            parts.append(f"{int(attrs['storage_gb'])}GB")
    if attrs.get("ram_gb") and "ram" in product_key:
        if f"{int(attrs['ram_gb'])}gb" not in lowered.replace(" ", ""):
            parts.append(f"{int(attrs['ram_gb'])}GB")
    return " ".join(part for part in parts if part).strip()


@dataclass
class CexSelection:
    status: str
    rows: list[CexRow]
    sell: float | None = None
    cash: float | None = None
    voucher: float | None = None
    grade: str = ""
    name: str = ""
    box_id: str = ""
    sample: int = 0
    note: str = ""
    ambiguous: bool = False


CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "gpu": ("graphics", "gpu"),
    "cpu": ("processor",),
    "console": ("console",),
    "phone": ("iphone", "samsung", "phone", "pixel"),
    "tablet": ("ipad", "tablet"),
    "laptop": ("laptop", "macbook"),
    "monitor": ("monitor",),
    "ssd": ("storage", "ssd"),
    "ram": ("memory", "ram"),
    "motherboard": ("motherboard",),
}


def _category_matches(kind: str, category: str) -> bool:
    hints = CATEGORY_HINTS.get(kind)
    if not hints:
        return True
    return any(hint in (category or "").lower() for hint in hints)


def select_quote(
    rows: list[CexRow], query: str, condition: str, kind: str = ""
) -> CexSelection:
    """Choose one defensible product quote, rejecting ambiguous matches.

    Grade rows of the same product are one product, not independent evidence.
    Different products (disc vs digital console, 8GB vs 16GB GPU) with a wide
    price spread are rejected rather than silently blended.
    """
    usable = [
        row for row in rows
        if _category_matches(kind, row.category)
        and not _accessory_row(query, row.name, row.category)
    ]
    scored = [(match_score(query, row.name), row) for row in usable]
    matched = [(score, row) for score, row in scored if score >= MIN_MATCH]
    if not matched:
        return CexSelection(
            "no_match", [], note="CeX returned no row for this exact product."
        )
    # CeX names every packaging/colour SKU separately ("…Black, Boxed",
    # "…Black, Unboxed", "…Black, Discounted"). Those are one product at three
    # prices, so the identity is the *least decorated* cohort of rows: the ones
    # that add no distinctive edition words to the query. A Halo Infinite or
    # Ghost of Yotei console, an All-Digital model or a 16GB card is a different
    # product and is therefore excluded instead of becoming the top quote.
    decorated = [(score, row, _specificity(query, row.name)) for score, row in matched]
    best_specificity = min(spec for _, _, spec in decorated)
    cohort = [(score, row) for score, row, spec in decorated if spec == best_specificity]
    sell_values = [row.sell_gbp for _, row in cohort if row.sell_gbp > 0]
    if not sell_values:
        return CexSelection("no_match", [], note="CeX rows had no usable sell price.")
    cohort_median = float(statistics.median(sell_values))
    deviations = [abs(value - cohort_median) for value in sell_values]
    dispersion = (statistics.median(deviations) / cohort_median) if cohort_median else 0.0
    if len(sell_values) >= 3 and dispersion > MAX_COHORT_DISPERSION:
        return CexSelection(
            "ambiguous",
            [row for _, row in cohort],
            note=(
                "The closest CeX rows disagree materially "
                f"(median £{cohort_median:.0f}, spread {dispersion:.0%}); "
                "refusing to blend different products."
            ),
            ambiguous=True,
        )
    excluded = [
        row.name for _, row, spec in decorated if spec > best_specificity
    ]
    excluded = list(dict.fromkeys(excluded))
    edition_note = (
        f" Excluded {len(excluded)} differently specified row(s): "
        + "; ".join(name[:60] for name in excluded[:2])
        + "."
        if excluded else ""
    )
    top_rows = [row for _, row in cohort]
    # CeX quotes the same console at three packaging prices (Boxed, Unboxed,
    # Discounted). The boxed figure is the standard retail reference; mixing a
    # "Discounted" price into the median would understate it.
    def package(row: CexRow) -> str:
        tail = row.name.lower().rstrip().rsplit(",", 1)[-1].strip()
        return tail.strip("(). ")

    for label in ("boxed", "unboxed", "discounted"):
        group = [row for row in top_rows if package(row) == label]
        if group:
            top_rows = group
            break
    preferred = _preferred_grades(condition)
    chosen_rows = [row for row in top_rows if row.grade in preferred] or top_rows
    for grade in preferred:
        grade_rows = [row for row in chosen_rows if row.grade == grade]
        if grade_rows:
            chosen_rows = grade_rows
            break
    chosen_rows = [row for row in chosen_rows if row.sell_gbp > 0]
    if not chosen_rows:
        return CexSelection("no_match", top_rows, note="No usable grade row.")
    sell = round(statistics.median(row.sell_gbp for row in chosen_rows), 2)
    cash_values = [row.cash_gbp for row in chosen_rows if row.cash_gbp > 0]
    voucher_values = [row.voucher_gbp for row in chosen_rows if row.voucher_gbp > 0]
    best = max(chosen_rows, key=lambda row: row.cash_gbp)
    return CexSelection(
        "ok",
        chosen_rows,
        sell=sell,
        cash=round(statistics.median(cash_values), 2) if cash_values else None,
        voucher=round(statistics.median(voucher_values), 2) if voucher_values else None,
        grade=chosen_rows[0].grade,
        name=chosen_rows[0].name,
        box_id=best.box_id,
        sample=len(chosen_rows),
        note=(
            f"CeX grade {chosen_rows[0].grade or '-'} retail £{sell:.0f}; "
            + (
                f"cash £{statistics.median(cash_values):.0f}, "
                f"voucher £{statistics.median(voucher_values):.0f}"
                if cash_values and voucher_values else
                "CeX cash/voucher offer not stated"
            )
            + (" (discontinued)" if chosen_rows[0].discontinued else "")
            + edition_note
        ),
    )


class CexPricing:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.client = httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={
                "User-Agent": "FlipIntel/1.0 (local price reference)",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        self.last_status: str = "idle"
        self.last_error: str | None = None
        self.last_success_at: str | None = None
        self._failures = 0
        self._pause_until = 0.0
        self._last_request = 0.0
        self._lock = asyncio.Lock()
        self._inflight: set[str] = set()

    async def close(self) -> None:
        await self.client.aclose()

    async def _throttle(self) -> None:
        wait = MIN_INTERVAL - (time.monotonic() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        await asyncio.sleep(random.uniform(0.05, 0.2))
        self._last_request = time.monotonic()

    async def _search(self, query: str) -> tuple[str, list[CexRow], str | None]:
        if time.monotonic() < self._pause_until:
            return "paused", [], self.last_error or "CeX lookups paused after an error"
        body = {
            "requests": [
                {
                    "indexName": INDEX,
                    "params": f"query={quote(query)}&hitsPerPage={HITS_PER_PAGE}",
                }
            ]
        }
        await self._throttle()
        try:
            response = await self.client.post(SEARCH_URL, json=body)
        except httpx.HTTPError as exc:
            self._note_failure(f"CeX network error: {type(exc).__name__}")
            return "failed", [], self.last_error
        if response.status_code in (401, 403, 429):
            self._note_failure(
                f"CeX search blocked the request (HTTP {response.status_code}). "
                "CeX prices are unavailable on this network."
            )
            self._pause_until = time.monotonic() + 3600
            return "blocked", [], self.last_error
        if response.status_code != 200:
            self._note_failure(f"CeX returned HTTP {response.status_code}")
            return "failed", [], self.last_error
        try:
            payload = response.json()
            hits = (payload.get("results") or [{}])[0].get("hits") or []
        except (ValueError, AttributeError, IndexError):
            self._note_failure("CeX returned an unexpected response shape")
            return "failed", [], self.last_error
        rows = [
            row for row in (_row_from_hit(hit) for hit in hits if isinstance(hit, dict))
            if row and row.sell_gbp > 0 and (row.origin or ORIGIN).upper() == ORIGIN
        ]
        self._failures = 0
        self.last_status = "ok"
        self.last_error = None
        return "ok", rows, None

    def _note_failure(self, message: str) -> None:
        self.last_status = "blocked" if "blocked" in message.lower() else "failed"
        self.last_error = message
        self._failures += 1
        if self._failures >= 3:
            self._pause_until = time.monotonic() + 3600

    def _save(
        self,
        product_key: str,
        query: str,
        selection: CexSelection,
        status: str,
        condition: str,
    ) -> None:
        now = iso()
        self.db.execute(
            """
            INSERT INTO external_quotes(
                product_key, source, title, sell_price, cash_price, voucher_price,
                grade, sample_count, url, status, fetched_at, checked_at
            ) VALUES (?, 'cex', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_key) DO UPDATE SET
                title=excluded.title, sell_price=excluded.sell_price,
                cash_price=excluded.cash_price, voucher_price=excluded.voucher_price,
                grade=excluded.grade, sample_count=excluded.sample_count,
                url=excluded.url, status=excluded.status,
                fetched_at=excluded.fetched_at, checked_at=excluded.checked_at
            """,
            (
                product_key,
                selection.name or query,
                selection.sell,
                selection.cash,
                selection.voucher,
                selection.grade or None,
                selection.sample,
                (
                    f"https://uk.webuy.com/product-detail?id={selection.box_id}"
                    if selection.box_id else None
                ),
                status,
                now,
                now,
            ),
        )
        if status != "ok" or selection.sell is None:
            return
        for row in selection.rows:
            self.db.execute(
                """
                INSERT INTO cex_price_history(
                    product_key, box_id, product_id, matched_name, grade,
                    sell_price, cash_price, voucher_price, cash_pct, voucher_pct,
                    condition, status, observed_at, raw
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?, ?)
                """,
                (
                    product_key, row.box_id, row.product_id, row.name, row.grade,
                    row.sell_gbp, row.cash_gbp, row.voucher_gbp,
                    row.cash_pct, row.voucher_pct, condition, now,
                    (
                        f"{row.category}|{row.super_category}|"
                        f"{'discontinued' if row.discontinued else 'active'}"
                    ),
                ),
            )

    async def refresh_quote(
        self,
        product_key: str,
        product_name: str,
        condition: str = "good",
        attrs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Research one product and store an observation. Never invents a price."""
        if not settings.cex_enabled:
            self.last_status = "disabled"
            return {"product_key": product_key, "status": "disabled"}
        if product_key in self._inflight:
            return {"product_key": product_key, "status": "duplicate"}
        async with self._lock:
            if product_key in self._inflight:
                return {"product_key": product_key, "status": "duplicate"}
            self._inflight.add(product_key)
            query = _query_for(product_name, product_key, attrs or {})
            search_text = _search_text(query, product_key)
            kind = product_key.split(":", 1)[0]
            try:
                status, rows, error = await self._search(search_text)
                if status != "ok":
                    self._save(
                        product_key, query,
                        CexSelection(status, [], note=error or "CeX lookup failed"),
                        status, condition,
                    )
                    return {"product_key": product_key, "status": status, "error": error}
                selection = select_quote(rows, query, condition, kind)
                self._save(product_key, query, selection, selection.status, condition)
                if selection.status == "ok":
                    self.last_success_at = iso()
                return {
                    "product_key": product_key,
                    "status": selection.status,
                    "sell": selection.sell,
                    "cash": selection.cash,
                    "voucher": selection.voucher,
                    "grade": selection.grade,
                    "name": selection.name,
                    "sample": selection.sample,
                    "note": selection.note,
                }
            finally:
                self._inflight.discard(product_key)

    def _reuse_hours(self) -> int:
        return max(1, self.db.setting_int("cex_reuse_hours", REUSE_HOURS))

    def needs_refresh(self, product_key: str) -> bool:
        """Whether this product's CeX quote should be fetched again.

        A genuine answer and a definitive "CeX does not stock this" are both
        cached for the reuse window; otherwise junk model keys would be
        re-queried on every scan. Transport failures and blocks are retried.
        """
        row = self.db.query_one(
            "SELECT status, COALESCE(checked_at, fetched_at) AS at "
            "FROM external_quotes WHERE product_key=? AND source='cex'",
            (product_key,),
        )
        if not row:
            return True
        status = str(row.get("status") or "")
        if status not in {"ok", "no_match", "ambiguous"}:
            return True
        checked = parse_iso(row.get("at"))
        if not checked:
            return True
        return (utcnow() - checked).total_seconds() >= self._reuse_hours() * 3600

    def _is_fresh(self, row: dict[str, Any] | None) -> bool:
        if not row or row.get("status") != "ok" or not row.get("sell_price"):
            return False
        fetched = parse_iso(row.get("checked_at") or row.get("fetched_at"))
        return bool(
            fetched
            and (utcnow() - fetched).total_seconds() < self._reuse_hours() * 3600
        )

    def stale_keys(
        self, models: list[tuple[str, str, str, dict[str, Any]]], limit: int
    ) -> list[tuple[str, str, str, dict[str, Any]]]:
        """Newest-first-free selection of models whose CeX quote is stale.

        Missing quotes come first, then the oldest observation.
        """
        rows = {
            str(row["product_key"]): row
            for row in self.db.query(
                "SELECT * FROM external_quotes WHERE source='cex'"
            )
        }
        candidates: list[tuple[int, float, tuple[str, str, str, dict[str, Any]]]] = []
        for model in models:
            existing = rows.get(model[0])
            if self._is_fresh(existing):
                continue
            fetched = parse_iso((existing or {}).get("checked_at") or (existing or {}).get("fetched_at"))
            age = (utcnow() - fetched).total_seconds() if fetched else float("inf")
            candidates.append((0 if not existing else 1, -age, model))
        candidates.sort(key=lambda item: (item[0], item[1]))
        return [model for _, _, model in candidates[:limit]]

    async def refresh_quotes(
        self,
        models: list[tuple[str, str, str, dict[str, Any]]],
        limit: int = 6,
    ) -> dict[str, int]:
        """models: [(product_key, display_name, condition, attrs)].

        Refreshes only stale returns, at most ``limit`` per call, and never
        duplicates an in-flight request for the same product.
        """
        if not settings.cex_enabled:
            self.last_status = "disabled"
            return {"ok": 0, "failed": 0, "skipped": 0}
        selected = self.stale_keys(models, limit)
        result = {"ok": 0, "failed": 0, "skipped": len(models) - len(selected)}
        for product_key, name, condition, attrs in selected:
            outcome = await self.refresh_quote(product_key, name, condition, attrs)
            if outcome.get("status") == "ok":
                result["ok"] += 1
            else:
                result["failed"] += 1
            if outcome.get("status") in {"blocked", "paused"}:
                break
        return result

    def stats(self) -> dict[str, Any]:
        row = self.db.query_one(
            """
            SELECT COUNT(*) AS observations, COUNT(DISTINCT product_key) AS products,
                   MAX(observed_at) AS latest
            FROM cex_price_history
            """
        )
        return {
            "observations": int(row["observations"] or 0) if row else 0,
            "products": int(row["products"] or 0) if row else 0,
            "latest_observation_at": row["latest"] if row else None,
        }

    def health(self) -> dict[str, Any]:
        latest = self.db.query_one(
            "SELECT MAX(checked_at) AS checked_at FROM external_quotes WHERE source='cex'"
        )
        return {
            "enabled": settings.cex_enabled,
            "status": self.last_status if settings.cex_enabled else "disabled",
            "error": self.last_error,
            "last_success_at": self.last_success_at,
            "last_checked_at": latest["checked_at"] if latest else None,
            "reuse_hours": self._reuse_hours(),
            "stats": self.stats(),
        }
