"""Personal flipping record and outcome learning.

A flip stores the prediction FlipIntel made *before* the purchase, the actual
costs and the realised result. Nothing is overwritten: the original prediction
stays attached to the completed flip so the two can be compared later.

Outcome learning is deliberately slow. A single bad sale must not move every
future valuation, so a multiplier is applied only after at least three completed
sales of the same kind, is weighted by the number of outcomes and is clamped to a
narrow band. Unsold items and time-to-sell are recorded too, because a high price
that never sells is not a better prediction.
"""
from __future__ import annotations

import json
from typing import Any

from .db import Database
from .utils import clamp, iso, parse_iso, utcnow

MIN_OUTCOMES = 3
MAX_FACTOR = 1.08
MIN_FACTOR = 0.90


def _money(value: Any, default: float = 0.0) -> float:
    try:
        return round(max(0.0, float(value)), 2)
    except (TypeError, ValueError):
        return default


def realised_totals(flip: dict[str, Any]) -> tuple[float, float | None, float | None]:
    """(total invested, net profit, ROI) for one flip."""
    cost = (
        _money(flip.get("purchase_price"))
        + _money(flip.get("purchase_shipping"))
        + _money(flip.get("preparation_cost"))
        + _money(flip.get("other_costs"))
    )
    final = flip.get("final_price")
    if final is None:
        return round(cost, 2), None, None
    proceeds = (
        _money(final)
        - _money(flip.get("fees"))
        - _money(flip.get("postage"))
    )
    net = round(proceeds - cost, 2)
    roi = round(net / cost, 4) if cost > 0 else None
    return round(cost, 2), net, roi


def resale_factors(db: Database) -> dict[str, float]:
    """Learned resale multiplier per kind, from completed flips only.

    ``realised / predicted`` is averaged with a count-based shrink so three
    sales cannot swing the whole market. The result is clamped so a run of bad
    outcomes can never make FlipIntel value everything at half price.
    """
    rows = db.query(
        """
        SELECT kind, final_price, predicted_resale
        FROM flips
        WHERE status = 'sold' AND final_price IS NOT NULL
          AND predicted_resale IS NOT NULL AND predicted_resale > 0
          AND kind IS NOT NULL
        """
    )
    grouped: dict[str, list[float]] = {}
    for row in rows:
        ratio = float(row["final_price"]) / float(row["predicted_resale"])
        if 0.2 <= ratio <= 3.0:
            grouped.setdefault(str(row["kind"]), []).append(ratio)
    factors: dict[str, float] = {}
    for kind, ratios in grouped.items():
        if len(ratios) < MIN_OUTCOMES:
            continue
        mean = sum(ratios) / len(ratios)
        shrink = len(ratios) / (len(ratios) + 5.0)
        factor = 1.0 + (mean - 1.0) * shrink
        factors[kind] = round(clamp(factor, MIN_FACTOR, MAX_FACTOR), 4)
    return factors


class FlipBook:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- creation ----------------------------------------------------------
    def start_from_listing(self, listing_id: int, **overrides: Any) -> dict[str, Any]:
        listing = self.db.query_one(
            """
            SELECT l.*, d.expected_resale, d.resale_low, d.resale_high, d.confidence,
                   d.evidence_level, d.net_profit, d.roi, d.max_bid, d.reasons
            FROM listings l LEFT JOIN deals d ON d.listing_id = l.id
            WHERE l.id = ?
            """,
            (listing_id,),
        )
        if not listing:
            raise KeyError("listing")
        existing = self.db.query_one(
            "SELECT * FROM flips WHERE listing_id = ? AND status IN ('held','listed')",
            (listing_id,),
        )
        if existing:
            return dict(existing)
        price = float(listing.get("price") or 0)
        data = {
            "listing_id": listing_id,
            "product_key": listing.get("product_key"),
            "title": listing.get("title") or "Untitled",
            "kind": listing.get("listing_kind"),
            "marketplace": listing.get("marketplace"),
            "condition_norm": listing.get("condition_norm"),
            "purchase_price": _money(overrides.get("purchase_price", price), price),
            "purchase_shipping": _money(
                overrides.get("purchase_shipping", listing.get("shipping") or 0)
            ),
            "preparation_cost": _money(overrides.get("preparation_cost")),
            "ask_price": overrides.get("ask_price"),
            "predicted_resale": listing.get("expected_resale"),
            "predicted_profit": listing.get("net_profit"),
            "predicted_confidence": listing.get("confidence"),
            "predicted_evidence": listing.get("evidence_level"),
            "prediction_reasons": listing.get("reasons"),
            "channel": overrides.get("channel") or "ebay",
            "status": "held",
        }
        return self._insert(data)

    def start_manual(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._insert(
            {
                "listing_id": None,
                "product_key": (data.get("product_key") or "").strip().lower() or None,
                "title": (data.get("title") or "").strip()[:500] or "Manual flip",
                "kind": data.get("kind"),
                "marketplace": data.get("marketplace"),
                "condition_norm": data.get("condition"),
                "purchase_price": _money(data.get("purchase_price")),
                "purchase_shipping": _money(data.get("purchase_shipping")),
                "preparation_cost": _money(data.get("preparation_cost")),
                "ask_price": data.get("ask_price"),
                "predicted_resale": data.get("predicted_resale"),
                "predicted_profit": data.get("predicted_profit"),
                "predicted_confidence": data.get("predicted_confidence"),
                "predicted_evidence": data.get("predicted_evidence"),
                "prediction_reasons": None,
                "channel": data.get("channel") or "ebay",
                "status": "held",
            }
        )

    def _insert(self, data: dict[str, Any]) -> dict[str, Any]:
        now = iso()
        cursor = self.db.execute(
            """
            INSERT INTO flips(
                listing_id, product_key, title, kind, marketplace, condition_norm,
                purchase_price, purchase_shipping, preparation_cost, ask_price,
                predicted_resale, predicted_profit, predicted_confidence,
                predicted_evidence, prediction_reasons, channel, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["listing_id"], data["product_key"], data["title"], data["kind"],
                data["marketplace"], data["condition_norm"], data["purchase_price"],
                data["purchase_shipping"], data["preparation_cost"], data["ask_price"],
                data["predicted_resale"], data["predicted_profit"],
                data["predicted_confidence"], data["predicted_evidence"],
                json.dumps(data["prediction_reasons"] or []),
                data["channel"], data["status"], now, now,
            ),
        )
        return self.get(int(cursor.lastrowid)) or {}

    # --- updates -----------------------------------------------------------
    def get(self, flip_id: int) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM flips WHERE id = ?", (flip_id,))
        if not row:
            return None
        flip = dict(row)
        flip["prediction_reasons"] = _json_list(flip.get("prediction_reasons"))
        flip["price_changes"] = self.db.query(
            "SELECT price, changed_at FROM flip_price_changes WHERE flip_id=? "
            "ORDER BY changed_at ASC",
            (flip_id,),
        )
        cost, net, roi = realised_totals(flip)
        flip["total_invested"] = cost
        flip["net_profit"] = net
        flip["roi"] = roi
        if net is not None and flip.get("predicted_profit") is not None:
            flip["prediction_error"] = round(
                float(net) - float(flip["predicted_profit"]), 2
            )
        else:
            flip["prediction_error"] = None
        if flip.get("listed_at"):
            end = flip.get("sold_at") or iso()
            start, stop = parse_iso(flip["listed_at"]), parse_iso(end)
            flip["days_to_sell"] = (
                round((stop - start).total_seconds() / 86400, 1)
                if start and stop else None
            )
        else:
            flip["days_to_sell"] = None
        return flip

    def update(self, flip_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "purchase_price", "purchase_shipping", "preparation_cost", "other_costs",
            "ask_price", "final_price", "fees", "postage", "channel", "status",
            "condition_norm", "sold_at", "feedback", "feedback_note",
        }
        fields: list[str] = []
        params: list[Any] = []
        for key, value in changes.items():
            if key not in allowed:
                continue
            if key in {
                "purchase_price", "purchase_shipping", "preparation_cost",
                "other_costs", "ask_price", "final_price", "fees", "postage",
            }:
                value = _money(value) if value is not None else None
            fields.append(f"{key}=?")
            params.append(value)
        if not fields:
            return self.get(flip_id) or {}
        now = iso()
        current = self.db.query_one("SELECT * FROM flips WHERE id=?", (flip_id,))
        if not current:
            raise KeyError("flip")
        if changes.get("status") == "listed" and not current.get("listed_at"):
            fields.append("listed_at=?")
            params.append(now)
        if changes.get("status") == "sold":
            if not current.get("listed_at"):
                fields.append("listed_at=?")
                params.append(now)
            if not changes.get("sold_at"):
                fields.append("sold_at=?")
                params.append(now)
        fields.append("updated_at=?")
        params.append(now)
        params.append(flip_id)
        self.db.execute(f"UPDATE flips SET {', '.join(fields)} WHERE id=?", params)
        return self.get(flip_id) or {}

    def change_price(self, flip_id: int, price: float) -> dict[str, Any]:
        flip = self.db.query_one("SELECT * FROM flips WHERE id=?", (flip_id,))
        if not flip:
            raise KeyError("flip")
        value = _money(price)
        self.db.execute(
            "INSERT INTO flip_price_changes(flip_id, price, changed_at) VALUES (?, ?, ?)",
            (flip_id, value, iso()),
        )
        self.db.execute(
            "UPDATE flips SET ask_price=?, updated_at=? WHERE id=?",
            (value, iso(), flip_id),
        )
        return self.get(flip_id) or {}

    # --- reporting ---------------------------------------------------------
    def list(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status and status != "all":
            rows = self.db.query(
                "SELECT id FROM flips WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = self.db.query(
                "SELECT id FROM flips ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
        return [self.get(int(row["id"])) or {} for row in rows]

    def summary(self) -> dict[str, Any]:
        flips = self.list(None, 1000)
        sold = [f for f in flips if f.get("status") == "sold" and f.get("net_profit") is not None]
        open_items = [f for f in flips if f.get("status") in {"held", "listed"}]
        realised = round(sum(float(f["net_profit"]) for f in sold), 2)
        predicted = round(
            sum(float(f["predicted_profit"] or 0) for f in sold), 2
        ) if sold else 0.0
        errors = [float(f["prediction_error"]) for f in sold if f.get("prediction_error") is not None]
        days = [float(f["days_to_sell"]) for f in sold if f.get("days_to_sell") is not None]
        capital = round(
            sum(float(f["total_invested"] or 0) for f in open_items), 2
        )
        return {
            "counts": {
                "held": sum(1 for f in flips if f.get("status") == "held"),
                "listed": sum(1 for f in flips if f.get("status") == "listed"),
                "sold": len(sold),
                "unsold": sum(1 for f in flips if f.get("status") == "unsold"),
            },
            "realised_profit": realised,
            "predicted_profit": predicted,
            "prediction_mae": round(sum(abs(e) for e in errors) / len(errors), 2) if errors else None,
            "average_days_to_sell": round(sum(days) / len(days), 1) if days else None,
            "capital_tied_up": capital,
            "learned_factors": resale_factors(self.db),
            "feedback": {
                "up": sum(1 for f in flips if f.get("feedback") == 1),
                "down": sum(1 for f in flips if f.get("feedback") == -1),
            },
        }


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []
