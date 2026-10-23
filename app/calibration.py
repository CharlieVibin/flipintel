"""Evidence-based calibration of asking prices against completed sales.

The engine never invents a category discount. It only estimates an adjustment
when several distinct products have both recent asking prices and completed
sales, and it validates the estimate on products held out of the fit.

User-entered and CSV-imported sales are reported separately from independently
observed completed sales. A calibration based only on user-supplied evidence
can be displayed as provisional, but it is not applied to recommendations.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .db import Database
from .identification import CONDITION_FACTORS
from .utils import clamp, hours_ago, median, quantile

FALLBACK_FACTOR = 0.85
MIN_SOLD_PER_PRODUCT = 2
MIN_ASKS_PER_PRODUCT = 4
MIN_PRODUCTS_PER_CATEGORY = 5
MAX_RATIO = 1.35
MIN_RATIO = 0.35
MAX_HOLDOUT_ERROR = 0.15


@dataclass
class ProductRatio:
    product_key: str
    category: str
    kind: str
    sold_median: float
    asking_median: float
    ratio: float
    sold_count: int
    asking_count: int
    independent_sold: int
    user_sold: int


@dataclass
class CategoryCalibration:
    category: str
    label: str
    status: str
    active: bool
    products: int
    training_products: int
    holdout_products: int
    sold_observations: int
    asking_observations: int
    independent_sold: int
    user_sold: int
    raw_factor: float | None
    factor: float | None
    confidence: float
    holdout_mae_pct: float | None
    holdout_bias_pct: float | None
    reason: str
    evidence_basis: str


@dataclass
class CalibrationReport:
    status: str
    active: bool
    fallback_factor: float
    global_factor: float | None
    matched_products: int
    sold_observations: int
    asking_observations: int
    independent_sold: int
    user_sold: int
    categories: list[CategoryCalibration] = field(default_factory=list)
    category_factors: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    calculated_at: str | None = None
    thresholds: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["category_factors"] = dict(self.category_factors)
        return value


def _condition_factor(value: str | None) -> float:
    return CONDITION_FACTORS.get(value or "unknown", 0.95)


def _load_ratios(db: Database) -> tuple[list[ProductRatio], dict[str, int]]:
    sold_rows = db.query(
        """
        SELECT product_key, kind, price, condition_norm, source, verified,
               observed_at, source_id
        FROM market_comps
        WHERE is_sold = 1 AND price >= 5 AND product_key IS NOT NULL
          AND observed_at >= ?
          AND COALESCE(condition_norm, 'unknown') != 'faulty'
          AND (kind IS NULL OR kind NOT IN ('other'))
        ORDER BY observed_at DESC
        """,
        (hours_ago(24 * 365 * 3),),
    )
    ask_rows = db.query(
        """
        SELECT product_key, listing_kind AS kind, price, condition_norm, duplicate_of
        FROM listings
        WHERE status = 'active' AND product_key IS NOT NULL
          AND listing_kind NOT IN ('other')
          AND is_faulty = 0 AND is_wanted = 0 AND is_bundle = 0
          AND COALESCE(condition_norm, 'unknown') != 'faulty'
          AND duplicate_of IS NULL AND price >= 5 AND price <= 25000
          AND last_seen >= ?
        """,
        (hours_ago(24 * 30),),
    )
    sold_by: dict[str, list[dict[str, Any]]] = {}
    seen_sales: set[tuple[str, str]] = set()
    for row in sold_rows:
        key = str(row.get("product_key") or "")
        source = str(row.get("source") or "")
        source_id = str(row.get("source_id") or row.get("id") or "")
        if (source, source_id) in seen_sales:
            continue
        seen_sales.add((source, source_id))
        sold_by.setdefault(key, []).append(row)
    asks_by: dict[str, list[dict[str, Any]]] = {}
    for row in ask_rows:
        key = str(row.get("product_key") or "")
        asks_by.setdefault(key, []).append(row)

    ratios: list[ProductRatio] = []
    counts = {"products_with_sales": 0, "products_with_both": 0, "excluded_thin": 0}
    for key, sales in sold_by.items():
        counts["products_with_sales"] += 1
        asks = asks_by.get(key) or []
        if len(sales) < MIN_SOLD_PER_PRODUCT or len(asks) < MIN_ASKS_PER_PRODUCT:
            counts["excluded_thin"] += 1
            continue
        sold_values = [
            float(row["price"]) / _condition_factor(row.get("condition_norm"))
            for row in sales
            if float(row.get("price") or 0) > 0
        ]
        ask_values = [
            float(row["price"]) / _condition_factor(row.get("condition_norm"))
            for row in asks
            if float(row.get("price") or 0) > 0
        ]
        sold_median = median(sold_values)
        asking_median = median(ask_values)
        if not sold_median or not asking_median or asking_median <= 0:
            continue
        ratio = sold_median / asking_median
        if not (MIN_RATIO <= ratio <= MAX_RATIO):
            counts["excluded_thin"] += 1
            continue
        kind = str(sales[0].get("kind") or "")
        category = kind if kind in {
            "gpu", "cpu", "ram", "ssd", "motherboard", "pc", "laptop",
            "console", "monitor", "phone", "tablet",
        } else "other"
        if category == "other":
            continue
        independent = sum(1 for row in sales if row.get("source") not in {"manual", "manual_csv"})
        user = len(sales) - independent
        ratios.append(
            ProductRatio(
                product_key=key,
                category=category,
                kind=kind,
                sold_median=round(sold_median, 2),
                asking_median=round(asking_median, 2),
                ratio=round(ratio, 4),
                sold_count=len(sales),
                asking_count=len(asks),
                independent_sold=independent,
                user_sold=user,
            )
        )
        counts["products_with_both"] += 1
    return ratios, counts


def _trim_ratios(rows: list[ProductRatio]) -> list[ProductRatio]:
    if len(rows) < 4:
        return rows
    values = [row.ratio for row in rows]
    centre = median(values)
    if not centre:
        return rows
    deviations = [abs(math.log(max(0.01, row.ratio / centre))) for row in rows]
    mad = median(deviations) or 0.0
    limit = max(0.12, mad * 1.4826 * 2.8)
    kept = [row for row in rows if abs(math.log(max(0.01, row.ratio / centre))) <= limit]
    return kept if len(kept) >= 3 else rows


def _split_holdout(rows: list[ProductRatio]) -> tuple[list[ProductRatio], list[ProductRatio]]:
    if len(rows) < 5:
        return rows, []
    ordered = sorted(
        rows,
        key=lambda row: hashlib.sha1(row.product_key.encode("utf-8")).hexdigest(),
    )
    holdout_size = max(2, round(len(rows) * 0.25))
    holdout_size = min(holdout_size, len(rows) - 3)
    holdout = ordered[:holdout_size]
    train = ordered[holdout_size:]
    return train, holdout


def _measure(train: list[ProductRatio], holdout: list[ProductRatio], factor: float) -> tuple[float | None, float | None]:
    if not holdout:
        return None, None
    errors = []
    signed = []
    for row in holdout:
        predicted = row.asking_median * factor
        if predicted <= 0:
            continue
        error = (predicted - row.sold_median) / row.sold_median
        errors.append(abs(error))
        signed.append(error)
    if not errors:
        return None, None
    return sum(errors) / len(errors), sum(signed) / len(signed)


def _category_report(
    category: str,
    label: str,
    rows: list[ProductRatio],
    fallback_factor: float,
) -> CategoryCalibration:
    train, holdout = _split_holdout(rows)
    trained = _trim_ratios(train)
    raw = median(row.ratio for row in trained)
    products = len(rows)
    independent = sum(row.independent_sold for row in rows)
    user = sum(row.user_sold for row in rows)
    sold_obs = sum(row.sold_count for row in rows)
    asks_obs = sum(row.asking_count for row in rows)
    basis = "independent" if independent >= max(2, int(sold_obs * 0.5)) else "user_supplied" if user else "mixed"
    raw_factor = round(clamp(raw, MIN_RATIO, 1.15), 4) if raw else None
    confidence = 0.0
    mae = bias = None
    reasons: list[str] = []
    active = False

    if products < MIN_PRODUCTS_PER_CATEGORY:
        status = "insufficient"
        reasons.append(
            f"Only {products} matching product(s); at least {MIN_PRODUCTS_PER_CATEGORY} are needed."
        )
    elif not raw_factor:
        status = "insufficient"
        reasons.append("No stable asking-to-sold ratio could be measured.")
    else:
        # Shrink small samples towards the conservative fallback rather than
        # pretending a handful of products is a category-wide law.
        weight = products / (products + 3.0)
        factor = round(fallback_factor + (raw_factor - fallback_factor) * weight, 4)
        mae, bias = _measure(train, holdout, factor)
        if basis != "independent":
            status = "provisional"
            reasons.append(
                "Evidence is user-supplied rather than independently observed; "
                "the factor is shown for transparency but is not applied."
            )
        elif len(holdout) < 2:
            status = "provisional"
            reasons.append("Not enough distinct products for a held-out validation.")
        elif mae is None or mae > MAX_HOLDOUT_ERROR:
            status = "rejected"
            reasons.append(
                f"Held-out error {mae:.0%} exceeds the {MAX_HOLDOUT_ERROR:.0%} limit."
                if mae is not None else "Held-out validation could not be measured."
            )
        else:
            active = True
            status = "active"
            confidence = clamp(
                0.35
                + min(0.30, products / 20.0)
                + max(0.0, 0.20 * (1.0 - mae / MAX_HOLDOUT_ERROR))
                + (0.10 if independent >= sold_obs else 0.0),
                0.0,
                0.92,
            )
            reasons.append(
                f"Validated on {len(holdout)} held-out product(s); mean absolute error {mae:.1%}."
            )
        factor_out = factor if status in {"active", "provisional"} else None
        return CategoryCalibration(
            category=category,
            label=label,
            status=status,
            active=active,
            products=products,
            training_products=len(train),
            holdout_products=len(holdout),
            sold_observations=sold_obs,
            asking_observations=asks_obs,
            independent_sold=independent,
            user_sold=user,
            raw_factor=raw_factor,
            factor=factor_out,
            confidence=round(confidence, 3),
            holdout_mae_pct=round(mae * 100, 2) if mae is not None else None,
            holdout_bias_pct=round(bias * 100, 2) if bias is not None else None,
            reason=" ".join(reasons),
            evidence_basis=basis,
        )

    return CategoryCalibration(
        category=category,
        label=label,
        status=status,
        active=False,
        products=products,
        training_products=len(train),
        holdout_products=len(holdout),
        sold_observations=sold_obs,
        asking_observations=asks_obs,
        independent_sold=independent,
        user_sold=user,
        raw_factor=raw_factor,
        factor=None,
        confidence=0.0,
        holdout_mae_pct=round(mae * 100, 2) if mae is not None else None,
        holdout_bias_pct=round(bias * 100, 2) if bias is not None else None,
        reason=" ".join(reasons),
        evidence_basis=basis,
    )


def build_calibration(db: Database) -> CalibrationReport:
    from .config import CATEGORY_LABELS

    rows, counts = _load_ratios(db)
    categories: list[CategoryCalibration] = []
    factors: dict[str, float] = {}
    grouped: dict[str, list[ProductRatio]] = {}
    for row in rows:
        grouped.setdefault(row.category, []).append(row)
    for category, group in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        report = _category_report(category, CATEGORY_LABELS.get(category, category.title()), group, FALLBACK_FACTOR)
        categories.append(report)
        if report.active and report.factor:
            factors[category] = report.factor

    sold_obs = sum(row.sold_count for row in rows)
    asking_obs = sum(row.asking_count for row in rows)
    independent = sum(row.independent_sold for row in rows)
    user = sum(row.user_sold for row in rows)
    active_categories = [item for item in categories if item.active]
    if active_categories:
        weights = [max(1, item.products) for item in active_categories]
        global_factor = round(
            sum((item.factor or FALLBACK_FACTOR) * weight for item, weight in zip(active_categories, weights))
            / sum(weights),
            4,
        )
        status = "active"
        reason = (
            f"{len(active_categories)} categor{'y' if len(active_categories) == 1 else 'ies'} validated on held-out products."
        )
    elif rows:
        global_factor = None
        status = "insufficient"
        if all(row.independent_sold == 0 for row in rows):
            reason = "Matching sales exist, but they are user-supplied and are not applied as calibration."
        else:
            reason = "Matching sales exist, but not enough distinct products meet the minimum and validation thresholds."
    else:
        global_factor = None
        status = "insufficient"
        reason = (
            "No correctly matched products have both recent asking prices and at least "
            f"{MIN_SOLD_PER_PRODUCT} completed sales. The conservative {FALLBACK_FACTOR:.0%} "
            "asking-price fallback remains in use."
        )

    return CalibrationReport(
        status=status,
        active=bool(active_categories),
        fallback_factor=FALLBACK_FACTOR,
        global_factor=global_factor,
        matched_products=len(rows),
        sold_observations=sold_obs,
        asking_observations=asking_obs,
        independent_sold=independent,
        user_sold=user,
        categories=categories,
        category_factors=factors,
        reason=reason,
        calculated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        thresholds={
            "min_sold_per_product": MIN_SOLD_PER_PRODUCT,
            "min_asking_per_product": MIN_ASKS_PER_PRODUCT,
            "min_products_per_category": MIN_PRODUCTS_PER_CATEGORY,
            "max_holdout_error_pct": MAX_HOLDOUT_ERROR * 100,
            "asks_window_days": 30,
            "sales_window_years": 3,
            "counts": counts,
        },
    )
