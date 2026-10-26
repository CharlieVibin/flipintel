"""HTTP API used by the dashboard."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from .calibration import build_calibration
from .config import CATEGORY_LABELS, MARKETPLACE_LABELS, settings
from .credentials import credential_store
from .db import Database
from .evidence import (
    PRODUCT_KEY_RE,
    VALID_KINDS,
    evidence_coverage,
    evidence_rows,
    evidence_type,
    export_csv,
    preview_import,
    template_csv,
    validate_comp_update,
)
from .flips import FlipBook
from .identification import CONDITION_FACTORS
from .scan_config import (
    MAX_BUDGET,
    config_payload,
    coverage_report,
    create_custom_preset,
    delete_custom_preset,
    list_presets,
    load_scan_config,
    save_scan_config,
    update_custom_preset,
)
from .scanner import Scanner, ScannerMaintenance, ScanConfigurationRequired
from .utils import age_hours, hours_ago, iso, parse_iso, normalise_text, utcnow
from .valuation import ValuationConfig
from .visibility import (
    leaf_for_row,
    preset_filter,
    preset_summary,
    row_in_preset,
    row_total,
)

router = APIRouter(prefix="/api")

SORT_SQL = {
    "score": "d.score DESC, d.net_profit DESC",
    "newest": "l.first_seen DESC",
    "profit": "d.net_profit DESC, d.score DESC",
    "roi": "d.roi DESC, d.net_profit DESC",
    "confidence": "d.confidence DESC, d.score DESC",
    "price": "l.price ASC",
}

# Grouped review is intentionally limited to a single, well-defined class of
# ambiguity. Opening a generic flag-based grouping API would let an arbitrary
# value turn into a bulk-action target; the variant gap is the one that is
# both common enough to drown the queue and safe to describe precisely.
REVIEW_GROUPS = {
    "variant_unclear": {
        "label": "Variant not stated",
        "description": (
            "Storage, memory or chip variant is missing, so the comparable pool may mix "
            "materially different configurations."
        ),
    }
}
REVIEW_BULK_LIMIT = 200


def _db(request: Request) -> Database:
    return request.app.state.db


def _scanner(request: Request) -> Scanner:
    return request.app.state.scanner


def _parse_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _functionality(row: dict[str, Any]) -> str:
    raw = row.get("attrs")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    if isinstance(raw, dict):
        value = str(raw.get("functionality") or "").strip().lower()
        if value:
            return value
    return "faulty" if row.get("is_faulty") else "unknown"


def _listing_row(row: dict[str, Any]) -> dict[str, Any]:
    functionality = _functionality(row)
    return {
        "id": row["id"],
        "marketplace": row["marketplace"],
        "marketplace_label": MARKETPLACE_LABELS.get(row["marketplace"], row["marketplace"].title()),
        "external_id": row["external_id"],
        "url": row["url"],
        "title": row["title"],
        "image_url": row["image_url"],
        "price": round(float(row["price"]), 2),
        "shipping": round(float(row.get("shipping") or 0), 2),
        "condition": row.get("condition_norm") or "unknown",
        "condition_raw": row.get("condition_raw") or "",
        "location": row.get("location") or "",
        "seller": row.get("seller") or "",
        "kind": row.get("listing_kind"),
        "category": row.get("category"),
        "product_key": row.get("product_key"),
        "product_name": row.get("product_name"),
        "identification_confidence": row.get("identification_confidence") or 0,
        "identity_level": row.get("identity_level") or None,
        "identity_basis": _parse_json(row.get("identity_basis"), []),
        "scan_leaf": row.get("scan_leaf"),
        "is_faulty": bool(row.get("is_faulty")),
        "functionality": functionality,
        # A resale estimate for untested stock is the working-condition value,
        # not a promise that the item works. Keep the distinction explicit in
        # every API response instead of letting the UI imply guaranteed profit.
        "conditional_profit": functionality == "untested",
        "is_wanted": bool(row.get("is_wanted")),
        "is_auction": bool(row.get("is_auction")),
        "auction_end_at": row.get("auction_end_at"),
        "current_bid": row.get("current_bid"),
        "bid_count": row.get("bid_count"),
        "min_next_bid": row.get("min_next_bid"),
        "is_bundle": bool(row.get("is_bundle")),
        "duplicate_of": row.get("duplicate_of"),
        "duplicate_score": row.get("duplicate_score"),
        "sale_role": row.get("sale_role"),
        "posted_at": row.get("posted_at"),
        "first_seen": row.get("first_seen"),
        "last_seen": row.get("last_seen"),
        "price_dropped_at": row.get("price_dropped_at"),
        "previous_price": row.get("previous_price"),
        "age_hours": round(age_hours(row.get("first_seen")) or 0, 2),
    }


def _deal_row(row: dict[str, Any]) -> dict[str, Any]:
    listing = _listing_row(row)
    purchase_total = float(row.get("purchase_total") or 0)
    expected = row.get("expected_resale")
    return {
        **listing,
        "expected_resale": round(float(expected), 2) if expected is not None else None,
        "resale_low": row.get("resale_low"),
        "resale_high": row.get("resale_high"),
        "confidence": row.get("confidence") or 0,
        "evidence_level": row.get("evidence_level"),
        "evidence_score": row.get("evidence_score"),
        "valuation_basis": row.get("valuation_basis") or "asking_only",
        "sample_size": row.get("sample_size") or 0,
        "valuation_method": row.get("valuation_method"),
        "rank_score": row.get("rank_score"),
        "rank_tier": row.get("rank_tier"),
        "valuation_refreshed_at": row.get("valuation_refreshed_at"),
        "resale_strategy": row.get("resale_strategy"),
        "resale_quick": row.get("resale_quick"),
        "resale_balanced": row.get("resale_balanced"),
        "resale_max": row.get("resale_max"),
        "max_bid": row.get("max_bid"),
        "estimated_days": row.get("estimated_days"),
        "purchase_total": round(purchase_total, 2),
        "net_profit": round(float(row["net_profit"]), 2) if row.get("net_profit") is not None else None,
        "net_profit_conservative": (
            round(float(row["net_profit_conservative"]), 2)
            if row.get("net_profit_conservative") is not None else None
        ),
        "net_profit_optimistic": (
            round(float(row["net_profit_optimistic"]), 2)
            if row.get("net_profit_optimistic") is not None else None
        ),
        "roi": row.get("roi"),
        "evidence_type": row.get("valuation_method") or None,
        "score": row.get("score") or 0,
        "is_deal": bool(row.get("is_deal")),
        "needs_review": bool(row.get("needs_review")),
        "eligibility": row.get("eligibility"),
        "eligibility_reason": row.get("eligibility_reason"),
        "review_reason": row.get("review_reason"),
        "risk_score": row.get("risk_score") or 0,
        "risk_level": row.get("risk_level") or "low",
        "risk_reasons": _parse_json(row.get("risk_reasons"), []),
        "review_decision": row.get("review_decision"),
        "review_decided_at": row.get("review_decided_at"),
        "reasons": _parse_json(row.get("reasons"), []),
        "computed_at": row.get("computed_at"),
        "saved": bool(row.get("saved")),
        "dismissed": bool(row.get("dismissed")),
        "outside_preset": bool(row.get("outside_preset")),
        "preset_reasons": row.get("preset_reasons") or [],
        "discount_pct": (
            round((float(expected) - purchase_total) / float(expected), 4)
            if expected and float(expected) > 0 else None
        ),
    }


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    db = _db(request)
    scanner = _scanner(request)
    scan_config = load_scan_config(db)
    preset_clauses, preset_params, scan_config = preset_filter(db, scan_config)
    preset_sql = " AND ".join(preset_clauses)
    counts = {
        "listings": db.scalar("SELECT COUNT(*) FROM listings") or 0,
        "active_listings": db.scalar("SELECT COUNT(*) FROM listings WHERE status='active'") or 0,
        "deals": db.scalar("SELECT COUNT(*) FROM deals d JOIN listings l ON l.id=d.listing_id "
                           "WHERE d.is_deal=1 AND l.status='active' AND l.is_auction=0"
                           + (f" AND {preset_sql}" if preset_sql else ""),
                           preset_params) or 0,
        "review": db.scalar("SELECT COUNT(*) FROM deals d JOIN listings l ON l.id=d.listing_id "
                            "WHERE d.needs_review=1 AND d.eligibility = 'needs_verification' "
                            "AND l.status='active' AND l.is_auction=0 "
                            "AND NOT EXISTS (SELECT 1 FROM review_decisions r WHERE r.listing_id=l.id)"
                            + (f" AND {preset_sql}" if preset_sql else ""),
                            preset_params) or 0,
        "duplicates": db.scalar(
            "SELECT COUNT(*) FROM listings WHERE status='active' AND duplicate_of IS NOT NULL"
        ) or 0,
        "hidden_by_preset": max(
            0,
            int(db.scalar(
                "SELECT COUNT(*) FROM deals d JOIN listings l ON l.id=d.listing_id "
                "WHERE d.is_deal=1 AND l.status='active' AND l.is_auction=0"
            ) or 0) - int(db.scalar(
                "SELECT COUNT(*) FROM deals d JOIN listings l ON l.id=d.listing_id "
                "WHERE d.is_deal=1 AND l.status='active' AND l.is_auction=0"
                + (f" AND {preset_sql}" if preset_sql else ""),
                preset_params,
            ) or 0),
        ),
    }
    last_scan = db.scalar("SELECT MAX(last_success_at) FROM marketplace_status")
    decisions = dict(
        (row["decision"], row["count"])
        for row in db.query("SELECT decision, COUNT(*) AS count FROM review_decisions GROUP BY decision")
    )
    return {
        "status": "ok",
        "time": iso(),
        "version": "1.0.0",
        "scanner": scanner.health_snapshot(),
        "maintenance": scanner.maintenance(),
        "maintenance_ready": scanner.maintenance_ready,
        "scan_config": {
            "confirmed": scan_config.confirmed,
            "active_preset": scan_config.active_preset,
            "enabled_categories": scan_config.enabled_count,
            "min_price": scan_config.min_price,
            "max_price": scan_config.max_price,
            "marketplaces": scan_config.marketplaces,
            "confirmed_at": scan_config.confirmed_at,
        },
        "counts": counts,
        "last_successful_scan": last_scan,
        "review_decisions": decisions,
        "ebay_configured": settings.ebay_configured,
        "ebay_setup": {
            "configured": settings.ebay_configured,
            "environment": credential_store.status()["environment"],
            "marketplace": "EBAY_GB",
            "credential_source": credential_store.status()["credential_source"],
        },
        "settings_thresholds": {
            "min_profit": db.setting_float("min_profit", 20),
            "min_roi": db.setting_float("min_roi", 0.12),
            "min_confidence": db.setting_float("min_confidence", 0.42),
        },
    }


@router.get("/overview")
async def overview(request: Request) -> dict[str, Any]:
    db = _db(request)
    cfg = ValuationConfig.from_db(db)
    scan_cfg = load_scan_config(db)
    preset_clauses, preset_params, scan_cfg = preset_filter(db, scan_cfg)
    preset_sql = " AND ".join(preset_clauses)
    base = (
        "FROM deals d JOIN listings l ON l.id = d.listing_id "
        "WHERE d.is_deal = 1 AND l.status = 'active' AND l.is_auction = 0 "
        "AND NOT EXISTS (SELECT 1 FROM dismissed_deals x WHERE x.listing_id = l.id)"
    )
    if preset_sql:
        base += f" AND {preset_sql}"
    unfiltered_base = (
        "FROM deals d JOIN listings l ON l.id = d.listing_id "
        "WHERE d.is_deal = 1 AND l.status = 'active' AND l.is_auction = 0 "
        "AND NOT EXISTS (SELECT 1 FROM dismissed_deals x WHERE x.listing_id = l.id)"
    )
    active = db.query_one(
        f"""
        SELECT COUNT(*) AS deals,
               COALESCE(SUM(CASE WHEN d.net_profit > 0 THEN d.net_profit ELSE 0 END), 0) AS potential_profit,
               COALESCE(AVG(d.roi), 0) AS avg_roi,
               COALESCE(AVG(CASE WHEN d.expected_resale > 0
                   THEN (d.expected_resale - d.purchase_total) / d.expected_resale END), 0) AS avg_discount,
               COALESCE(MAX(d.net_profit), 0) AS best_profit,
               COALESCE(AVG(d.confidence), 0) AS avg_confidence
        {base}
        """,
        preset_params,
    ) or {}
    unfiltered_deals = db.scalar(f"SELECT COUNT(*) {unfiltered_base}") or 0
    hidden_deals = max(0, int(unfiltered_deals) - int(active.get("deals") or 0))
    new_24h = db.scalar(
        f"SELECT COUNT(*) {base} AND l.first_seen >= ?",
        (*preset_params, hours_ago(24)),
    ) or 0
    review_count = db.scalar(
        "SELECT COUNT(*) FROM deals d JOIN listings l ON l.id=d.listing_id "
        "WHERE d.needs_review=1 AND d.eligibility = 'needs_verification' "
        "AND l.status='active' AND l.is_auction=0 "
        "AND NOT EXISTS (SELECT 1 FROM review_decisions r WHERE r.listing_id=l.id)"
        + (f" AND {preset_sql}" if preset_sql else ""),
        preset_params,
    ) or 0
    sold_evidence = db.scalar("SELECT COUNT(*) FROM market_comps WHERE is_sold=1") or 0
    manual_evidence = db.scalar("SELECT COUNT(*) FROM market_comps WHERE source='manual'") or 0
    duplicate_listings = db.scalar(
        "SELECT COUNT(*) FROM listings WHERE status='active' AND duplicate_of IS NOT NULL"
    ) or 0
    best = db.query_one(
        f"""
        SELECT l.id, l.title, l.price, l.marketplace, l.image_url, l.category,
               d.expected_resale, d.net_profit, d.roi, d.score, d.confidence
        {base}
        ORDER BY d.score DESC LIMIT 1
        """,
        preset_params,
    )
    categories = db.query(
        f"""
        SELECT l.category, COUNT(*) AS count
        {base} AND l.category IS NOT NULL
        GROUP BY l.category ORDER BY count DESC
        """,
        preset_params,
    )
    marketplaces = db.query(
        f"""
        SELECT l.marketplace, COUNT(*) AS count
        {base}
        GROUP BY l.marketplace ORDER BY count DESC
        """,
        preset_params,
    )
    active_listings = db.scalar(
        "SELECT COUNT(*) FROM listings l WHERE l.status='active' AND l.is_auction = 0"
        + (f" AND {preset_sql}" if preset_sql else ""),
        preset_params,
    ) or 0
    active_listings_all = db.scalar(
        "SELECT COUNT(*) FROM listings WHERE status='active' AND is_auction = 0"
    ) or 0
    tracked_models = db.scalar(
        "SELECT COUNT(DISTINCT l.product_key) FROM listings l "
        "WHERE l.status='active' AND l.is_auction = 0 AND l.product_key IS NOT NULL"
        + (f" AND {preset_sql}" if preset_sql else ""),
        preset_params,
    ) or 0
    last_scans = db.query(
        "SELECT marketplace, state, last_success_at, last_error, next_scan_at, "
        "consecutive_failures FROM marketplace_status"
    )
    return {
        "preset": preset_summary(scan_cfg, hidden=hidden_deals, unfiltered=unfiltered_deals),
        "hidden_by_preset": hidden_deals,
        "deals": active.get("deals", 0),
        "potential_profit": round(float(active.get("potential_profit") or 0), 2),
        "avg_roi": round(float(active.get("avg_roi") or 0), 4),
        "avg_discount": round(float(active.get("avg_discount") or 0), 4),
        "best_profit": round(float(active.get("best_profit") or 0), 2),
        "avg_confidence": round(float(active.get("avg_confidence") or 0), 4),
        "new_deals_24h": new_24h,
        "review_count": review_count,
        "sold_evidence": sold_evidence,
        "manual_evidence": manual_evidence,
        "duplicate_listings": duplicate_listings,
        "best": best,
        "categories": categories,
        "marketplaces": marketplaces,
        "sources": last_scans,
        "active_listings": active_listings,
        "active_listings_all": active_listings_all,
        "tracked_models": tracked_models,
        "scans_24h": db.scalar(
            "SELECT COUNT(*) FROM scan_runs WHERE started_at >= ?", (hours_ago(24),)
        ) or 0,
        "last_scan": db.scalar("SELECT MAX(finished_at) FROM scan_runs WHERE status IN ('ok','degraded')"),
        "thresholds": {
            "min_profit": cfg.min_profit,
            "min_roi": cfg.min_roi,
            "min_confidence": cfg.min_confidence,
        },
    }


@router.get("/deals")
async def list_deals(
    request: Request,
    category: str | None = None,
    marketplace: str | None = None,
    condition: str | None = None,
    min_profit: float = 0,
    min_roi: float = 0,
    min_confidence: float = 0,
    max_age_hours: float = 0,
    sort: str = "score",
    q: str | None = None,
    saved_only: bool = False,
    archived: bool = False,
    review: bool = False,
    review_status: str = "pending",
    product_key: str | None = None,
    review_flag: str | None = None,
    active_preset: bool = True,
    include_auctions: bool = False,
    page: int = Query(1, ge=1),
    limit: int = Query(48, ge=1, le=120),
) -> dict[str, Any]:
    db = _db(request)
    scan_cfg = load_scan_config(db)
    preset_clauses, preset_params, scan_cfg = preset_filter(db, scan_cfg)
    where = ["l.status = 'active'"]
    params: list[Any] = []
    if review:
        if review_status == "pending":
            where.append("d.needs_review = 1")
            where.append("d.eligibility = 'needs_verification'")
            # Auctions are a separate workspace. A current bid is not a
            # purchase price and a days-away auction is not a manual review
            # task, so it must never occupy the Human Review queue.
            where.append("l.is_auction = 0")
            where.append("r.listing_id IS NULL")
        elif review_status in {"approved", "rejected", "corrected"}:
            # History includes items that were approved and consequently left
            # the review queue by becoming eligible deals.
            where.append("r.decision = ?")
            params.append(review_status)
        else:
            where.append("d.needs_review = 1")
            where.append("d.eligibility = 'needs_verification'")
            where.append("l.is_auction = 0")
    elif not saved_only and not archived:
        where.append("d.is_deal = 1")
    if archived:
        where.append("x.listing_id IS NOT NULL")
    else:
        where.append("x.listing_id IS NULL")
    if saved_only and not archived:
        where.append("s.listing_id IS NOT NULL")
    if category:
        cats = [c.strip() for c in category.split(",") if c.strip()]
        if cats:
            where.append(f"l.category IN ({','.join('?' for _ in cats)})")
            params.extend(cats)
    if marketplace:
        markets = [m.strip() for m in marketplace.split(",") if m.strip()]
        if markets:
            where.append(f"l.marketplace IN ({','.join('?' for _ in markets)})")
            params.extend(markets)
    if condition:
        conditions = [c.strip() for c in condition.split(",") if c.strip()]
        if conditions:
            where.append(f"l.condition_norm IN ({','.join('?' for _ in conditions)})")
            params.extend(conditions)
    if min_profit > 0:
        where.append("d.net_profit >= ?")
        params.append(min_profit)
    if min_roi > 0:
        where.append("d.roi >= ?")
        params.append(min_roi)
    if min_confidence > 0:
        where.append("d.confidence >= ?")
        params.append(min_confidence)
    if max_age_hours > 0:
        where.append("l.first_seen >= ?")
        params.append(hours_ago(max_age_hours))
    if q:
        where.append("(l.title LIKE ? OR l.product_name LIKE ?)")
        like = f"%{q.strip()}%"
        params.extend([like, like])
    if product_key:
        where.append("l.product_key = ?")
        params.append(product_key.strip().lower())
    if review_flag:
        flag = review_flag.strip().lower()
        if flag not in REVIEW_GROUPS:
            raise HTTPException(status_code=422, detail="Unknown review flag")
        where.append("l.flags LIKE ?")
        params.append(f'%"{flag}"%')
    # Auctions are a separate workspace. Their current bid must never be
    # presented as a Buy It Now purchase price in the deal feed. Explicitly
    # saved auctions stay visible in the Shortlist.
    if not saved_only and not review and not archived and not include_auctions:
        where.append("l.is_auction = 0")
    base_where = list(where)
    base_params = list(params)
    if active_preset:
        where = where + preset_clauses
        params = params + preset_params
    order = SORT_SQL.get(sort, SORT_SQL["score"])
    where_sql = " AND ".join(where)
    joins = (
        "FROM deals d JOIN listings l ON l.id = d.listing_id "
        "LEFT JOIN saved_deals s ON s.listing_id = l.id "
        "LEFT JOIN dismissed_deals x ON x.listing_id = l.id "
        "LEFT JOIN review_decisions r ON r.listing_id = l.id"
    )
    total = db.scalar(f"SELECT COUNT(*) {joins} WHERE {where_sql}", params) or 0
    unfiltered_total = db.scalar(
        f"SELECT COUNT(*) {joins} WHERE {' AND '.join(base_where)}", base_params
    ) or 0
    if active_preset:
        hidden = max(0, int(unfiltered_total) - int(total))
    else:
        # History mode still reports how many rows the active preset hides.
        preset_total = db.scalar(
            f"SELECT COUNT(*) {joins} WHERE {' AND '.join(base_where + preset_clauses)}",
            base_params + preset_params,
        ) or 0
        hidden = max(0, int(unfiltered_total) - int(preset_total))
    rows = db.query(
        f"""
        SELECT l.*, s.listing_id IS NOT NULL AS saved, x.listing_id IS NOT NULL AS dismissed,
               d.expected_resale, d.resale_low, d.resale_high, d.confidence, d.sample_size,
               d.valuation_method, d.purchase_total, d.net_profit, d.net_profit_conservative,
               d.net_profit_optimistic, d.roi, d.score, d.is_deal,
               d.needs_review, d.review_reason, d.risk_score, d.risk_level,
               d.risk_reasons, d.reasons, d.computed_at, d.evidence_level,
               d.resale_strategy, d.resale_quick, d.resale_balanced, d.resale_max,
               d.max_bid, d.estimated_days, d.valuation_refreshed_at,
               d.valuation_basis, d.evidence_score,
               d.rank_score, d.rank_tier, d.eligibility, d.eligibility_reason,
               r.decision AS review_decision, r.decided_at AS review_decided_at
        {joins}
        WHERE {where_sql}
        ORDER BY {order}
        LIMIT ? OFFSET ?
        """,
        params + [limit, (page - 1) * limit],
    )
    if not active_preset:
        # History mode: label, never re-price or re-alert, rows outside the
        # saved preset so they cannot be mistaken for current opportunities.
        for row in rows:
            inside, reasons = row_in_preset(scan_cfg, row)
            row["outside_preset"] = not inside
            row["preset_reasons"] = reasons
    return {
        "deals": [_deal_row(row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "preset": preset_summary(scan_cfg, hidden=hidden, unfiltered=unfiltered_total),
        "hidden_by_preset": hidden,
        "generated_at": iso(),
        "thresholds": {
            "min_profit": db.setting_float("min_profit", 20),
            "min_roi": db.setting_float("min_roi", 0.12),
            "min_confidence": db.setting_float("min_confidence", 0.42),
        },
    }


@router.get("/reviews/groups")
async def review_groups(
    request: Request,
    kind: str = "phone",
    flag: str = "variant_unclear",
    limit: int = Query(40, ge=1, le=100),
    active_preset: bool = True,
) -> dict[str, Any]:
    """Group pending reviews by product so a widespread gap is visible at once.

    The dashboard previously showed 2,000 individual review cards, hiding the
    fact that hundreds of them were the same phone model missing a storage
    figure. Grouping changes the question from "is this listing odd?" to "does
    this model family need a policy?" without weakening the review gate.
    """
    db = _db(request)
    kind = kind.strip().lower()
    flag = flag.strip().lower()
    if kind not in VALID_KINDS:
        raise HTTPException(status_code=422, detail="Unknown listing kind")
    if flag not in REVIEW_GROUPS:
        raise HTTPException(status_code=422, detail="Unknown review flag")
    base = (
        "FROM deals d JOIN listings l ON l.id = d.listing_id "
        "LEFT JOIN review_decisions r ON r.listing_id = l.id "
        "WHERE d.needs_review = 1 AND d.eligibility = 'needs_verification' "
        "AND l.status = 'active' AND l.is_auction = 0 AND r.listing_id IS NULL "
        "AND l.listing_kind = ? AND l.flags LIKE ?"
    )
    like = f'%"{flag}"%'
    params: list[Any] = [kind, like]
    if active_preset:
        clauses, clause_params, _ = preset_filter(db)
        if clauses:
            base += " AND " + " AND ".join(clauses)
            params.extend(clause_params)
    total_listings = db.scalar(f"SELECT COUNT(*) {base}", params) or 0
    total_groups = db.scalar(
        f"SELECT COUNT(DISTINCT l.product_key) {base}", params
    ) or 0
    rows = db.query(
        f"""
        SELECT l.product_key,
               COALESCE(MAX(l.product_name), l.product_key) AS product_name,
               COUNT(*) AS count,
               MIN(l.price) AS price_low,
               MAX(l.price) AS price_high,
               MIN(l.first_seen) AS oldest_first_seen,
               MAX(l.first_seen) AS newest_first_seen,
               GROUP_CONCAT(DISTINCT l.marketplace) AS marketplaces
        {base}
        GROUP BY l.product_key
        ORDER BY count DESC, product_name ASC
        LIMIT ?
        """,
        params + [limit],
    )
    groups = []
    for row in rows:
        key = str(row["product_key"])
        listing_ids = [
            int(item["id"])
            for item in db.query(
                f"SELECT l.id {base} AND l.product_key = ? "
                "ORDER BY l.price ASC, l.id ASC LIMIT ?",
                params + [key, REVIEW_BULK_LIMIT + 1],
            )
        ]
        truncated = len(listing_ids) > REVIEW_BULK_LIMIT
        listing_ids = listing_ids[:REVIEW_BULK_LIMIT]
        examples = db.query(
            f"""
            SELECT l.id, l.title, l.price, l.marketplace, l.url, l.image_url, l.first_seen
            {base} AND l.product_key = ?
            ORDER BY l.first_seen DESC, l.id DESC LIMIT 3
            """,
            params + [key],
        )
        groups.append(
            {
                "product_key": key,
                "product_name": row.get("product_name") or key,
                "count": int(row["count"] or 0),
                "price_low": round(float(row["price_low"] or 0), 2),
                "price_high": round(float(row["price_high"] or 0), 2),
                "oldest_first_seen": row.get("oldest_first_seen"),
                "newest_first_seen": row.get("newest_first_seen"),
                "marketplaces": sorted(
                    item for item in str(row.get("marketplaces") or "").split(",") if item
                ),
                "listing_ids": listing_ids,
                "listings_truncated": truncated,
                "examples": [
                    {
                        "id": int(item["id"]),
                        "title": item.get("title"),
                        "price": round(float(item["price"] or 0), 2),
                        "marketplace": item.get("marketplace"),
                        "url": item.get("url"),
                        "image_url": item.get("image_url"),
                        "first_seen": item.get("first_seen"),
                    }
                    for item in examples
                ],
            }
        )
    return {
        "kind": kind,
        "flag": flag,
        "label": REVIEW_GROUPS[flag]["label"],
        "description": REVIEW_GROUPS[flag]["description"],
        "groups": groups,
        "total_groups": int(total_groups),
        "total_listings": int(total_listings),
        "bulk_limit": REVIEW_BULK_LIMIT,
        "generated_at": iso(),
    }


@router.get("/deals/{listing_id}")
async def deal_detail(request: Request, listing_id: int) -> dict[str, Any]:
    db = _db(request)
    row = db.query_one(
        """
        SELECT l.*, d.*, s.listing_id IS NOT NULL AS saved,
               x.listing_id IS NOT NULL AS dismissed,
               r.decision AS review_decision, r.note AS review_note,
               r.decided_at AS review_decided_at
        FROM listings l
        LEFT JOIN deals d ON d.listing_id = l.id
        LEFT JOIN saved_deals s ON s.listing_id = l.id
        LEFT JOIN dismissed_deals x ON x.listing_id = l.id
        LEFT JOIN review_decisions r ON r.listing_id = l.id
        WHERE l.id = ?
        """,
        (listing_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Listing not found")
    deal = _deal_row({**row, "id": row["id"]})
    deal["flags"] = _parse_json(row.get("flags"), [])
    deal["attrs"] = _parse_json(row.get("attrs"), {})
    deal["breakdown"] = _parse_json(row.get("breakdown"), {})
    deal["description"] = row.get("description") or ""
    deal["price_history"] = db.query(
        "SELECT price, observed_at FROM price_history WHERE listing_id=? ORDER BY observed_at ASC LIMIT 60",
        (listing_id,),
    )
    deal["reasons"] = _parse_json(row.get("reasons"), [])
    deal["review_note"] = row.get("review_note")
    deal["saved"] = bool(row.get("saved"))
    deal["dismissed"] = bool(row.get("dismissed"))
    return deal


@router.post("/deals/{listing_id}/save")
async def save_deal(request: Request, listing_id: int) -> dict[str, Any]:
    db = _db(request)
    if not db.scalar("SELECT id FROM listings WHERE id=?", (listing_id,)):
        raise HTTPException(status_code=404, detail="Listing not found")
    db.execute(
        "INSERT OR IGNORE INTO saved_deals(listing_id, created_at) VALUES (?, ?)",
        (listing_id, iso()),
    )
    db.execute("DELETE FROM dismissed_deals WHERE listing_id=?", (listing_id,))
    return {"saved": True}


@router.delete("/deals/{listing_id}/save")
async def unsave_deal(request: Request, listing_id: int) -> dict[str, Any]:
    _db(request).execute("DELETE FROM saved_deals WHERE listing_id=?", (listing_id,))
    return {"saved": False}


@router.post("/deals/{listing_id}/dismiss")
async def dismiss_deal(
    request: Request, listing_id: int, reason: str | None = None
) -> dict[str, Any]:
    db = _db(request)
    if not db.scalar("SELECT id FROM listings WHERE id=?", (listing_id,)):
        raise HTTPException(status_code=404, detail="Listing not found")
    db.execute(
        "INSERT INTO dismissed_deals(listing_id, reason, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(listing_id) DO UPDATE SET reason=excluded.reason, created_at=excluded.created_at",
        (listing_id, (reason or "")[:300], iso()),
    )
    db.execute("DELETE FROM saved_deals WHERE listing_id=?", (listing_id,))
    return {"dismissed": True}


@router.post("/deals/{listing_id}/restore")
async def restore_deal(request: Request, listing_id: int) -> dict[str, Any]:
    _db(request).execute("DELETE FROM dismissed_deals WHERE listing_id=?", (listing_id,))
    return {"dismissed": False}


# ---------------------------------------------------------------------------
# Auctions: a separate workspace from the fixed-price deal feed
# ---------------------------------------------------------------------------

AUCTION_SORTS = {
    "ending": "l.auction_end_at ASC",
    "bid": "COALESCE(l.current_bid, l.price) ASC, l.auction_end_at ASC",
    "profit": "(d.net_profit IS NULL), d.net_profit DESC, l.auction_end_at ASC",
    "newest": "l.first_seen DESC, l.auction_end_at ASC",
}

AUCTION_STATUSES = ("all", "verified", "needs_verification", "rejected")


def _auction_status(row: dict[str, Any], config) -> tuple[str, str]:
    """Classify an auction without inventing a valuation it does not have.

    Only a valuation with moderate or strong compatible evidence can support a
    profit figure or a maximum bid. Everything else stays a research candidate
    and is never labelled as a verified opportunity.
    """
    inside, reasons = row_in_preset(config, row)
    if not inside:
        return "rejected", "; ".join(reasons)
    if row.get("is_faulty"):
        return "rejected", "Listed as faulty or for parts"
    if row.get("needs_review"):
        return "needs_verification", row.get("review_reason") or "Identity needs manual review"
    if not row.get("product_key") or row.get("identity_level") == "insufficient":
        return "needs_verification", "No reliable product identity"
    level = row.get("evidence_level")
    if row.get("expected_resale") is None or level in (None, "insufficient"):
        return "needs_verification", "No compatible pricing evidence for this variant"
    if level == "limited":
        return "needs_verification", "Limited compatible evidence; no supported bid ceiling"
    if row.get("max_bid") is None or row.get("net_profit") is None:
        return "needs_verification", "Valuation supports a resale range but not a bid ceiling"
    if _functionality(row) == "untested":
        return (
            "needs_verification",
            "Untested: the resale estimate assumes it works after testing; "
            "profit is conditional, not guaranteed",
        )
    if row.get("is_deal"):
        return "verified", "Supported resale estimate and profit after costs"
    if float(row.get("net_profit") or 0) <= 0:
        return "rejected", "Current bid is above the supported profitable ceiling"
    return "rejected", "Below the saved profit threshold"


def _auction_payload(row: dict[str, Any], config) -> dict[str, Any]:
    auction = _deal_row(row)
    status, reason = _auction_status(row, config)
    total, basis = row_total(config, row)
    inside, preset_reasons = row_in_preset(config, row)
    end = parse_iso(row.get("auction_end_at"))
    seconds = int((end - utcnow()).total_seconds()) if end else None
    auction.update(
        {
            "verification_status": status,
            "verification_reason": reason,
            "profit_supported": status == "verified",
            "outside_preset": not inside,
            "preset_reasons": preset_reasons,
            "total_cost": round(total, 2),
            "cost_basis": basis,
            "seconds_remaining": max(0, seconds) if seconds is not None else None,
            "ends_at": row.get("auction_end_at"),
            "identified_variant": row.get("product_name") or None,
            "evidence_level": row.get("evidence_level"),
            "live_bid": row.get("current_bid"),
        }
    )
    return auction


def _auction_diagnostics(db, config) -> dict[str, Any]:
    status = db.query_one(
        "SELECT state, last_attempt_at, last_success_at, last_error, next_scan_at "
        "FROM marketplace_status WHERE marketplace='ebay'"
    ) or {}
    last_run = db.query_one(
        "SELECT id, started_at, finished_at, status, items_found, items_new, "
        "duration_ms, error, queries, detail FROM scan_runs "
        "WHERE marketplace='ebay' AND detail LIKE '%ending_soon%' "
        "ORDER BY id DESC LIMIT 1"
    )
    detail = _parse_json((last_run or {}).get("detail"), {})
    return {
        "source": {
            "marketplace": "ebay",
            "enabled": config.marketplace_enabled("ebay"),
            "state": status.get("state") or "idle",
            "last_attempt_at": status.get("last_attempt_at"),
            "last_success_at": status.get("last_success_at"),
            "last_error": status.get("last_error"),
            "next_scan_at": status.get("next_scan_at"),
        },
        "last_scan": (
            {
                **{key: last_run.get(key) for key in (
                    "id", "started_at", "finished_at", "status", "items_found",
                    "items_new", "duration_ms", "error",
                )},
                "queries": _parse_json(last_run.get("queries"), []),
                "window_minutes": detail.get("window_minutes"),
                "configured_categories": detail.get("configured_categories") or [],
                "strategy": detail.get("strategy"),
            }
            if last_run else None
        ),
        "settings": {
            "alerts_enabled": db.setting_bool("auction_alerts_enabled", True),
            "window_minutes": db.setting_int("auction_window_minutes", 30),
            "alert_window_minutes": db.setting_int("auction_alert_window_minutes", 15),
            "scan_seconds": db.setting_int("auction_scan_seconds", 300),
            "urgent_seconds": db.setting_int("auction_urgent_seconds", 300),
        },
        "config_confirmed": config.confirmed,
    }


@router.get("/auctions")
async def list_auctions(
    request: Request,
    ending_within: int = Query(0, ge=0, le=1440),
    sort: str = "ending",
    status: str = "all",
    active_preset: bool = True,
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=120),
) -> dict[str, Any]:
    db = _db(request)
    config = load_scan_config(db)
    clauses, clause_params, config = preset_filter(db, config)
    status = status.strip().lower()
    if status not in AUCTION_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown auction status")
    order = AUCTION_SORTS.get(sort, AUCTION_SORTS["ending"])
    window_end = utcnow() + timedelta(minutes=ending_within) if ending_within else None
    base_sql = (
        "FROM listings l LEFT JOIN deals d ON d.listing_id = l.id "
        "LEFT JOIN dismissed_deals x ON x.listing_id = l.id "
        "WHERE l.status='active' AND l.is_auction=1 AND l.auction_end_at IS NOT NULL "
        "AND l.auction_end_at >= ? AND x.listing_id IS NULL"
    )
    base_params: list[Any] = [iso()]
    if window_end is not None:
        base_sql += " AND l.auction_end_at <= ?"
        base_params.append(iso(window_end))
    preset_sql = " AND " + " AND ".join(clauses) if clauses else ""
    # The hidden count must stay meaningful in history mode too, so the
    # preset-restricted count is always evaluated.
    visible_count = db.scalar(
        f"SELECT COUNT(*) {base_sql}{preset_sql}", base_params + clause_params
    ) or 0
    unfiltered_count = db.scalar(f"SELECT COUNT(*) {base_sql}", base_params) or 0
    rows = db.query(
        f"""
        SELECT l.*, d.expected_resale, d.resale_low, d.resale_high, d.confidence,
               d.sample_size, d.valuation_method, d.purchase_total, d.net_profit,
               d.net_profit_conservative, d.net_profit_optimistic, d.roi, d.score,
               d.is_deal, d.needs_review, d.review_reason, d.risk_score, d.risk_level,
               d.risk_reasons, d.reasons, d.computed_at, d.evidence_level,
               d.resale_strategy, d.resale_quick, d.resale_balanced, d.resale_max,
               d.max_bid, d.estimated_days, d.valuation_refreshed_at,
               d.valuation_basis, d.evidence_score, d.rank_score, d.rank_tier
        {base_sql}{preset_sql if active_preset else ''}
        ORDER BY {order}
        LIMIT 1000
        """,
        base_params + (clause_params if active_preset else []),
    )
    payload = [_auction_payload(row, config) for row in rows]
    counts = {
        "all": len(payload),
        "verified": sum(1 for item in payload if item["verification_status"] == "verified"),
        "needs_verification": sum(
            1 for item in payload if item["verification_status"] == "needs_verification"
        ),
        "rejected": sum(1 for item in payload if item["verification_status"] == "rejected"),
    }
    filtered_items = (
        payload if status == "all"
        else [item for item in payload if item["verification_status"] == status]
    )
    total = len(filtered_items)
    start = (page - 1) * limit
    return {
        "auctions": filtered_items[start : start + limit],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "counts": counts,
        "preset": preset_summary(
            config,
            hidden=max(0, int(unfiltered_count) - int(visible_count)),
            unfiltered=unfiltered_count,
        ),
        "diagnostics": _auction_diagnostics(db, config),
        "generated_at": iso(),
    }


@router.post("/auctions/refresh")
async def refresh_auctions(request: Request) -> dict[str, Any]:
    db = _db(request)
    scanner = _scanner(request)
    config = load_scan_config(db)
    if not config.confirmed:
        raise HTTPException(status_code=409, detail="Save the scan configuration first")
    if not config.marketplace_enabled("ebay"):
        raise HTTPException(status_code=409, detail="eBay is disabled in Scan configuration")
    if not db.setting_bool("auction_alerts_enabled", True):
        raise HTTPException(status_code=409, detail="Auction monitoring is disabled in Settings")
    if scanner._auction_lock.locked():
        return {"accepted": False, "message": "An auction refresh is already running", "time": iso()}
    asyncio.create_task(scanner.scan_auctions(), name="manual-auctions")
    return {"accepted": True, "message": "Auction refresh started", "time": iso()}


class ReviewUpdate(BaseModel):
    decision: str
    note: str | None = Field(default=None, max_length=1000)
    product_key: str | None = Field(default=None, max_length=120)
    product_name: str | None = Field(default=None, max_length=200)
    kind: str | None = None


class BulkReviewUpdate(BaseModel):
    listing_ids: list[int] = Field(default_factory=list, max_length=REVIEW_BULK_LIMIT)
    decision: str
    note: str | None = Field(default=None, max_length=1000)


@router.post("/reviews/bulk")
async def bulk_review_deal(
    request: Request, body: BulkReviewUpdate
) -> dict[str, Any]:
    """Apply one decision to explicit pending review rows.

    The caller must name every listing (there is no filter-based bulk update), so
    a changed queue cannot silently widen the action. Only pending rows that
    still need review are updated; stale or already-decided IDs are skipped and
    reported rather than overwritten. Corrections stay single-listing because
    each corrected key needs its own evidence.
    """
    db = _db(request)
    decision = body.decision.strip().lower()
    if decision not in {"approved", "rejected"}:
        raise HTTPException(
            status_code=422, detail="Bulk decisions must be approved or rejected"
        )
    listing_ids = list(dict.fromkeys(int(value) for value in body.listing_ids))
    if not listing_ids:
        raise HTTPException(status_code=422, detail="Select at least one pending listing")
    if len(listing_ids) > REVIEW_BULK_LIMIT:
        raise HTTPException(
            status_code=422,
            detail=f"Bulk review is limited to {REVIEW_BULK_LIMIT} listings at a time",
        )
    placeholders = ",".join("?" for _ in listing_ids)
    eligible = [
        int(row["id"])
        for row in db.query(
            f"""
            SELECT l.id
            FROM listings l
            JOIN deals d ON d.listing_id = l.id
            LEFT JOIN review_decisions r ON r.listing_id = l.id
            WHERE l.id IN ({placeholders}) AND l.status = 'active'
              AND l.is_auction = 0
              AND d.needs_review = 1 AND d.eligibility = 'needs_verification'
              AND r.listing_id IS NULL
            ORDER BY l.id
            """,
            listing_ids,
        )
    ]
    default_note = (
        "Bulk approved after manual review; automatic risk signals remain visible."
        if decision == "approved"
        else "Bulk rejected as not a genuine opportunity after manual review."
    )
    note = normalise_text(body.note or default_note)[:1000] or None
    now = iso()
    if eligible:
        db.executemany(
            """
            INSERT INTO review_decisions(listing_id, decision, note, product_key, kind, decided_at)
            VALUES (?, ?, ?, NULL, NULL, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                decision=excluded.decision, note=excluded.note,
                product_key=excluded.product_key, kind=excluded.kind,
                decided_at=excluded.decided_at
            """,
            [(listing_id, decision, note, now) for listing_id in eligible],
        )
    if eligible and decision == "approved":
        # A single refresh covers every approval. Rejections need no valuation
        # work because rejected rows are already outside the deal feed.
        await _scanner(request)._run_refresh(force=True)
    remaining = db.scalar(
        "SELECT COUNT(*) FROM deals d JOIN listings l ON l.id = d.listing_id "
        "WHERE d.needs_review = 1 AND d.eligibility = 'needs_verification' "
        "AND l.status = 'active' AND l.is_auction = 0 "
        "AND NOT EXISTS (SELECT 1 FROM review_decisions r WHERE r.listing_id = l.id)"
    ) or 0
    return {
        "decision": decision,
        "updated": len(eligible),
        "skipped": len(listing_ids) - len(eligible),
        "listing_ids": eligible,
        "remaining": int(remaining),
    }


@router.post("/deals/{listing_id}/review")
async def review_deal(
    request: Request, listing_id: int, body: ReviewUpdate
) -> dict[str, Any]:
    db = _db(request)
    listing = db.query_one("SELECT * FROM listings WHERE id=?", (listing_id,))
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.get("is_auction"):
        raise HTTPException(
            status_code=409,
            detail="Live auctions are managed in the Auctions tab, not Human Review",
        )
    decision = body.decision.strip().lower()
    if decision not in {"approved", "rejected", "corrected"}:
        raise HTTPException(status_code=422, detail="decision must be approved, rejected or corrected")
    corrected_key = None
    corrected_kind = None
    if decision == "corrected":
        corrected_key = (body.product_key or "").strip().lower()
        corrected_kind = (body.kind or "").strip().lower()
        if not PRODUCT_KEY_RE.match(corrected_key):
            raise HTTPException(status_code=422, detail="A valid product_key is required for a correction")
        if corrected_kind not in VALID_KINDS:
            raise HTTPException(status_code=422, detail="A valid kind is required for a correction")
        db.execute(
            "UPDATE listings SET product_key=?, product_name=?, listing_kind=?, category=?, "
            "identification_confidence=?, updated_at=? WHERE id=?",
            (
                corrected_key,
                normalise_text(body.product_name or listing.get("product_name") or corrected_key),
                corrected_kind,
                corrected_kind,
                max(0.7, float(listing.get("identification_confidence") or 0)),
                iso(),
                listing_id,
            ),
        )
        refreshed = db.query_one("SELECT * FROM listings WHERE id=?", (listing_id,))
        if refreshed:
            db.execute(
                "UPDATE listings SET scan_leaf=? WHERE id=?",
                (leaf_for_row(refreshed), listing_id),
            )
    db.execute(
        """
        INSERT INTO review_decisions(listing_id, decision, note, product_key, kind, decided_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(listing_id) DO UPDATE SET
            decision=excluded.decision, note=excluded.note,
            product_key=excluded.product_key, kind=excluded.kind,
            decided_at=excluded.decided_at
        """,
        (
            listing_id, decision, normalise_text(body.note or "")[:1000] or None,
            corrected_key, corrected_kind, iso(),
        ),
    )
    if decision in {"approved", "corrected"}:
        # Resolve the review outcome in the deal feed immediately. A rejection
        # needs no valuation work: the listing is already excluded from deals.
        await request.app.state.scanner._run_refresh(force=True)
    return {
        "listing_id": listing_id,
        "decision": decision,
        "product_key": corrected_key or listing.get("product_key"),
        "kind": corrected_kind or listing.get("listing_kind"),
        "confidence_boosted": False,
    }


@router.delete("/deals/{listing_id}/review")
async def undo_review(request: Request, listing_id: int) -> dict[str, Any]:
    _db(request).execute("DELETE FROM review_decisions WHERE listing_id=?", (listing_id,))
    await request.app.state.scanner._run_refresh(force=True)
    return {"decision": None}


@router.get("/scans")
async def scans(request: Request, limit: int = Query(30, ge=1, le=100)) -> dict[str, Any]:
    db = _db(request)
    rows = db.query(
        "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT ?", (limit,)
    )
    for row in rows:
        row["queries"] = _parse_json(row.get("queries"), [])
        row["detail"] = _parse_json(row.get("detail"), {})
    return {"scans": rows}


class CompCreate(BaseModel):
    product_key: str = Field(min_length=2, max_length=120)
    product_name: str | None = Field(default=None, max_length=200)
    title: str = Field(default="", max_length=500)
    price: float = Field(gt=0, le=100000)
    condition: str = "good"
    postage: float = Field(default=0, ge=0, le=500)
    kind: str | None = None
    url: str | None = None
    source_id: str | None = Field(default=None, max_length=200)
    observed_at: str | None = None
    notes: str | None = Field(default=None, max_length=1000)
    verified: bool = True


def _comp_row(row: dict[str, Any]) -> dict[str, Any]:
    source = str(row.get("source") or "")
    evidence = "manual_sold" if source == "manual" else (
        "imported_sold" if source == "manual_csv" else (
            "sold" if row.get("is_sold") else ("cex" if source == "cex" else "asking")
        )
    )
    return {
        **row,
        "price": round(float(row.get("price") or 0), 2),
        "postage": round(float(row.get("postage") or 0), 2),
        "is_sold": bool(row.get("is_sold")),
        "verified": bool(row.get("verified")),
        "evidence": evidence,
        "evidence_type": evidence_type(row),
    }


@router.get("/comps")
async def list_comps(
    request: Request,
    product_key: str = Query(..., min_length=2, max_length=120),
    kind: str | None = None,
    limit: int = Query(40, ge=1, le=200),
) -> dict[str, Any]:
    db = _db(request)
    rows = db.query(
        """
        SELECT id, product_key, product_name, marketplace, source, source_id, title, price, kind,
               condition_norm, condition_raw, is_sold, postage, notes, verified, evidence_confidence,
               url, observed_at, created_at, currency, variants, import_id
        FROM market_comps
        WHERE product_key = ? AND (? IS NULL OR kind = ? OR kind IS NULL)
        ORDER BY is_sold DESC, observed_at DESC LIMIT ?
        """,
        (product_key, kind, kind, limit),
    )
    sold = [row for row in rows if row["is_sold"]]
    asking = [row for row in rows if not row["is_sold"]]
    prices = [float(row["price"]) for row in sold]
    return {
        "product_key": product_key,
        "comps": [_comp_row(row) for row in rows],
        "summary": {
            "sold_count": len(sold),
            "asking_count": len(asking),
            "manual_count": sum(1 for row in sold if row["source"] in {"manual", "manual_csv"}),
            "independent_count": sum(1 for row in sold if row["source"] not in {"manual", "manual_csv"}),
            "sold_median": round(sorted(prices)[len(prices) // 2], 2) if prices else None,
        },
    }


@router.post("/comps")
async def create_comp(request: Request, body: CompCreate) -> dict[str, Any]:
    db = _db(request)
    product_key = body.product_key.strip().lower()
    if not product_key or any(ch.isspace() for ch in product_key):
        raise HTTPException(status_code=422, detail="Product key must not contain spaces")
    condition = body.condition.strip().lower()
    if condition not in CONDITION_FACTORS:
        raise HTTPException(status_code=422, detail="Unknown condition")
    observed = body.observed_at or iso()
    if parse_iso(observed) is None:
        raise HTTPException(status_code=422, detail="observed_at must be an ISO timestamp")
    url = (body.url or "").strip() or None
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=422, detail="URL must be http(s)")
    title = body.title.strip() or f"Manual sold comparable for {product_key}"
    source_id = (body.source_id or "").strip() or hashlib.sha1(
        f"{product_key}|{title}|{body.price:.2f}|{observed}".encode("utf-8")
    ).hexdigest()[:24]
    # A user-entered sale is useful evidence but is not an independently
    # confirmed marketplace transaction. Keep its provenance explicit.
    db.execute(
        """
        INSERT INTO market_comps(product_key, marketplace, source, source_id, title, price,
            kind, condition_norm, is_sold, postage, notes, verified, evidence_confidence,
            url, observed_at, created_at, currency, product_name, condition_raw,
            variants, updated_at)
        VALUES (?, 'manual', 'manual', ?, ?, ?, ?, ?, 1, ?, ?, 0, ?, ?, ?, ?, 'GBP', ?, ?, ?, ?)
        ON CONFLICT(source, source_id) DO UPDATE SET
            product_key=excluded.product_key, title=excluded.title, price=excluded.price,
            kind=excluded.kind, condition_norm=excluded.condition_norm, postage=excluded.postage,
            notes=excluded.notes, evidence_confidence=excluded.evidence_confidence,
            url=excluded.url, observed_at=excluded.observed_at,
            product_name=excluded.product_name, condition_raw=excluded.condition_raw,
            variants=excluded.variants, updated_at=excluded.updated_at
        """,
        (
            product_key, source_id, title, body.price, (body.kind or None), condition,
            body.postage, body.notes, 0.82 if body.verified else 0.68,
            url, observed, iso(), body.product_name, condition, None, iso(),
        ),
    )
    row = db.query_one("SELECT * FROM market_comps WHERE source='manual' AND source_id=?", (source_id,))
    await request.app.state.scanner._run_refresh(force=True)
    return {"comp": _comp_row(row) if row else None, "refreshed": True}


@router.delete("/comps/{comp_id}")
async def delete_comp(request: Request, comp_id: int) -> dict[str, Any]:
    db = _db(request)
    row = db.query_one("SELECT id, source FROM market_comps WHERE id=?", (comp_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Comparable not found")
    if row["source"] not in {"manual", "manual_csv"}:
        raise HTTPException(status_code=403, detail="Only user-supplied evidence can be deleted")
    db.execute("DELETE FROM market_comps WHERE id=?", (comp_id,))
    await request.app.state.scanner._run_refresh(force=True)
    return {"deleted": True}


class EvidenceImportRequest(BaseModel):
    filename: str = Field(default="evidence.csv", max_length=200)
    content: str = Field(min_length=1, max_length=2_100_000)
    commit: bool = False


class EvidenceUpdate(BaseModel):
    product_key: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=500)
    price: float | None = Field(default=None, gt=0, le=100000)
    postage: float | None = Field(default=None, ge=0, le=500)
    condition: str | None = None
    kind: str | None = None
    notes: str | None = Field(default=None, max_length=1000)
    url: str | None = None
    observed_at: str | None = None


@router.get("/evidence")
async def list_evidence(
    request: Request,
    product_key: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    return evidence_rows(
        _db(request),
        product_key=product_key,
        source=source,
        date_from=date_from,
        date_to=date_to,
        q=q,
        limit=limit,
        offset=(page - 1) * limit,
    ) | {"page": page}


@router.get("/evidence/coverage")
async def get_evidence_coverage(request: Request) -> dict[str, Any]:
    return evidence_coverage(_db(request))


@router.get("/evidence/template")
async def download_evidence_template() -> Response:
    return Response(
        content=template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="flipintel-evidence-template.csv"'},
    )


@router.get("/evidence/export")
async def export_evidence(
    request: Request,
    product_key: str | None = None,
    source: str | None = "user",
) -> Response:
    result = evidence_rows(
        _db(request), product_key=product_key, source=source, limit=10_000, offset=0
    )
    suffix = f"-{product_key}" if product_key else ""
    filename = f"flipintel-evidence{suffix}.csv".replace("/", "-")
    return Response(
        content=export_csv(result["evidence"]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/evidence/import")
async def import_evidence(request: Request, body: EvidenceImportRequest) -> dict[str, Any]:
    content = body.content.encode("utf-8")
    try:
        result = preview_import(_db(request), content, body.filename, commit=body.commit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.commit and result["counts"]["imported"] + result["counts"]["updated"] > 0:
        await request.app.state.scanner._run_refresh(force=True)
    return result


@router.patch("/evidence/{evidence_id}")
async def update_evidence(
    request: Request, evidence_id: int, body: EvidenceUpdate
) -> dict[str, Any]:
    db = _db(request)
    row = db.query_one("SELECT * FROM market_comps WHERE id=?", (evidence_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Evidence record not found")
    if row["source"] not in {"manual", "manual_csv"}:
        raise HTTPException(status_code=403, detail="Only user-supplied evidence can be corrected")
    try:
        updates = validate_comp_update(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    updates["updated_at"] = iso()
    assignments = ", ".join(f"{key}=?" for key in updates)
    db.execute(
        f"UPDATE market_comps SET {assignments} WHERE id=?",
        list(updates.values()) + [evidence_id],
    )
    updated = db.query_one("SELECT * FROM market_comps WHERE id=?", (evidence_id,))
    await request.app.state.scanner._run_refresh(force=True)
    return {"comp": _comp_row(updated) if updated else None}


@router.delete("/evidence/{evidence_id}")
async def delete_evidence(request: Request, evidence_id: int) -> dict[str, Any]:
    return await delete_comp(request, evidence_id)


@router.get("/calibration")
async def calibration(request: Request) -> dict[str, Any]:
    return build_calibration(_db(request)).as_dict()


@router.get("/categories")
async def categories(request: Request) -> dict[str, Any]:
    db = _db(request)
    rows = db.query(
        """
        SELECT l.category, COUNT(*) AS listings
        FROM listings l WHERE l.status='active' AND l.category IS NOT NULL
        GROUP BY l.category ORDER BY listings DESC
        """
    )
    return {
        "categories": [
            {"key": row["category"], "label": CATEGORY_LABELS.get(row["category"], row["category"]), "count": row["listings"]}
            for row in rows
        ]
    }


class ScanConfigUpdate(BaseModel):
    categories: list[str] = Field(default_factory=list)
    min_price: float = Field(default=0, ge=0, le=MAX_BUDGET)
    max_price: float = Field(default=100, ge=0, le=MAX_BUDGET)
    marketplaces: dict[str, bool] = Field(default_factory=dict)
    active_preset: str = Field(default="custom", max_length=80)
    confirmed: bool = True


class ScanPresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_slug: str | None = Field(default=None, max_length=80)
    config: dict[str, Any] | None = None


class ScanPresetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    config: dict[str, Any] | None = None


@router.get("/scan-config")
async def get_scan_config(request: Request) -> dict[str, Any]:
    db = _db(request)
    return config_payload(db, load_scan_config(db))


@router.put("/scan-config")
async def update_scan_config(request: Request, body: ScanConfigUpdate) -> dict[str, Any]:
    db = _db(request)
    marketplaces = {
        name: bool(body.marketplaces.get(name, True))
        for name in ("vinted", "gumtree", "ebay")
    }
    try:
        config = save_scan_config(
            db,
            categories=body.categories,
            min_price=body.min_price,
            max_price=body.max_price,
            marketplaces=marketplaces,
            active_preset=body.active_preset or "custom",
            confirmed=body.confirmed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    scanner = _scanner(request)
    await scanner.apply_scan_config(config)
    return config_payload(db, config)


@router.get("/scan-config/coverage")
async def scan_coverage(request: Request) -> dict[str, Any]:
    db = _db(request)
    return coverage_report(db, load_scan_config(db))


@router.get("/scan-config/presets")
async def scan_presets(request: Request) -> dict[str, Any]:
    return {"presets": list_presets(_db(request))}


@router.post("/scan-config/presets")
async def create_scan_preset(request: Request, body: ScanPresetCreate) -> dict[str, Any]:
    try:
        preset = create_custom_preset(
            _db(request), name=body.name, base_slug=body.base_slug, config=body.config
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"preset": preset, "presets": list_presets(_db(request))}


@router.put("/scan-config/presets/{preset_id}")
async def edit_scan_preset(
    request: Request, preset_id: int, body: ScanPresetUpdate
) -> dict[str, Any]:
    preset = update_custom_preset(
        _db(request), preset_id, name=body.name, config=body.config
    )
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"preset": preset, "presets": list_presets(_db(request))}


@router.delete("/scan-config/presets/{preset_id}")
async def remove_scan_preset(request: Request, preset_id: int) -> dict[str, Any]:
    deleted = delete_custom_preset(_db(request), preset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"deleted": True, "presets": list_presets(_db(request))}


class SettingsUpdate(BaseModel):
    min_profit: float | None = Field(default=None, ge=0, le=10000)
    min_roi: float | None = Field(default=None, ge=0, le=5)
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    ebay_fvf: float | None = Field(default=None, ge=0, le=0.5)
    resale_channel: str | None = None
    resale_shipping: float | None = Field(default=None, ge=0, le=200)
    packaging_cost: float | None = Field(default=None, ge=0, le=200)
    other_costs: float | None = Field(default=None, ge=0, le=500)
    vinted_shipping: float | None = Field(default=None, ge=0, le=200)
    asking_to_sold_factor: float | None = Field(default=None, ge=0.4, le=1.2)
    resale_strategy: str | None = None
    min_roi_high_value: float | None = Field(default=None, ge=0, le=5)
    high_value_threshold: float | None = Field(default=None, ge=0, le=100000)
    history_reuse_days: int | None = Field(default=None, ge=1, le=60)
    cex_refresh_limit: int | None = Field(default=None, ge=0, le=60)
    auction_scan_seconds: int | None = Field(default=None, ge=120, le=3600)
    auction_urgent_seconds: int | None = Field(default=None, ge=120, le=3600)
    auction_window_minutes: int | None = Field(default=None, ge=5, le=240)
    auction_alert_window_minutes: int | None = Field(default=None, ge=5, le=240)
    auction_alerts_enabled: bool | None = None
    notification_cooldown_minutes: int | None = Field(default=None, ge=10, le=1440)
    notifications_enabled: bool | None = None
    discord_webhook_url: str | None = None
    scan_interval_vinted: int | None = Field(default=None, ge=120, le=3600)
    scan_interval_gumtree: int | None = Field(default=None, ge=120, le=3600)
    scan_interval_ebay: int | None = Field(default=None, ge=120, le=3600)
    queries_per_cycle: int | None = Field(default=None, ge=1, le=12)
    enabled_vinted: bool | None = None
    enabled_gumtree: bool | None = None
    enabled_ebay: bool | None = None


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    db = _db(request)
    values = db.get_settings()

    def num(key: str, default: float) -> float:
        try:
            return float(values.get(key, default))
        except (TypeError, ValueError):
            return default

    def boolean(key: str, default: bool) -> bool:
        return str(values.get(key, str(default))).lower() in {"1", "true", "yes", "on"}

    return {
        "deal": {
            "min_profit": num("min_profit", 20),
            "min_roi": num("min_roi", 0.12),
            "min_confidence": num("min_confidence", 0.42),
        },
        "fees": {
            "ebay_fvf": num("ebay_fvf", 0.1322),
            "resale_channel": str(values.get("resale_channel", "ebay")),
            "resale_shipping": num("resale_shipping", 4.5),
            "packaging_cost": num("packaging_cost", 1.0),
            "other_costs": num("other_costs", 0.0),
            "vinted_shipping": num("vinted_shipping", 3.49),
            "asking_to_sold_factor": num("asking_to_sold_factor", 0.85),
            "resale_strategy": str(values.get("resale_strategy", "balanced")),
            "min_roi_high_value": num("min_roi_high_value", 0.18),
            "high_value_threshold": num("high_value_threshold", 250),
        },
        "pricing": {
            "history_reuse_days": int(num("history_reuse_days", 7)),
            "cex_refresh_limit": int(num("cex_refresh_limit", 6)),
        },
        "auctions": {
            "scan_seconds": int(num("auction_scan_seconds", 300)),
            "urgent_seconds": int(num("auction_urgent_seconds", 300)),
            "window_minutes": int(num("auction_window_minutes", 30)),
            "alert_window_minutes": int(num("auction_alert_window_minutes", 15)),
            "alerts_enabled": boolean("auction_alerts_enabled", True),
        },
        "scanning": {
            "scan_interval_vinted": int(num("scan_interval_vinted", 1800)),
            "scan_interval_gumtree": int(num("scan_interval_gumtree", 1800)),
            "scan_interval_ebay": int(num("scan_interval_ebay", 1800)),
            "queries_per_cycle": int(num("queries_per_cycle", 12)),
            "enabled_vinted": boolean("enabled_vinted", True),
            "enabled_gumtree": boolean("enabled_gumtree", True),
            "enabled_ebay": boolean("enabled_ebay", True),
        },
        "notifications": {
            "enabled": boolean("notifications_enabled", False),
            "webhook_configured": bool(values.get("discord_webhook_url")),
            "cooldown_minutes": int(num("notification_cooldown_minutes", 180)),
        },
        "environment": {
            "ebay_configured": settings.ebay_configured,
            "facebook_available": False,
            "cex_enabled": settings.cex_enabled,
            "ebay": credential_store.status(),
        },
    }


@router.put("/settings")
async def update_settings(request: Request, body: SettingsUpdate) -> dict[str, Any]:
    db = _db(request)
    updates: dict[str, str] = {}
    mapping = {
        "min_profit": "min_profit",
        "min_roi": "min_roi",
        "min_confidence": "min_confidence",
        "ebay_fvf": "ebay_fvf",
        "resale_shipping": "resale_shipping",
        "packaging_cost": "packaging_cost",
        "other_costs": "other_costs",
        "vinted_shipping": "vinted_shipping",
        "asking_to_sold_factor": "asking_to_sold_factor",
        "scan_interval_vinted": "scan_interval_vinted",
        "scan_interval_gumtree": "scan_interval_gumtree",
        "scan_interval_ebay": "scan_interval_ebay",
        "queries_per_cycle": "queries_per_cycle",
        "min_roi_high_value": "min_roi_high_value",
        "high_value_threshold": "high_value_threshold",
        "history_reuse_days": "history_reuse_days",
        "cex_refresh_limit": "cex_refresh_limit",
        "auction_scan_seconds": "auction_scan_seconds",
        "auction_urgent_seconds": "auction_urgent_seconds",
        "auction_window_minutes": "auction_window_minutes",
        "auction_alert_window_minutes": "auction_alert_window_minutes",
        "notification_cooldown_minutes": "notification_cooldown_minutes",
    }
    integer_keys = {
        "scan_interval_vinted", "scan_interval_gumtree", "scan_interval_ebay",
        "queries_per_cycle", "history_reuse_days", "cex_refresh_limit",
        "auction_scan_seconds", "auction_urgent_seconds", "auction_window_minutes",
        "auction_alert_window_minutes", "notification_cooldown_minutes",
    }
    for field, key in mapping.items():
        value = getattr(body, field)
        if value is not None:
            updates[key] = str(int(value) if key in integer_keys else value)
    if body.resale_strategy is not None:
        strategy = body.resale_strategy.strip().lower()
        if strategy not in {"quick", "balanced", "max"}:
            raise HTTPException(
                status_code=422,
                detail="Resale strategy must be quick, balanced or max",
            )
        updates["resale_strategy"] = strategy
    if body.resale_channel is not None:
        channel = body.resale_channel.strip().lower()
        if channel not in {"ebay", "vinted", "facebook"}:
            raise HTTPException(status_code=422, detail="Resale channel must be ebay, vinted or facebook")
        updates["resale_channel"] = channel
    for field in (
        "notifications_enabled", "enabled_vinted", "enabled_gumtree", "enabled_ebay",
        "auction_alerts_enabled",
    ):
        value = getattr(body, field)
        if value is not None:
            updates[field] = "true" if value else "false"
    if body.discord_webhook_url is not None:
        webhook = body.discord_webhook_url.strip()
        if webhook and not (
            webhook.startswith("https://discord.com/api/webhooks/")
            or webhook.startswith("https://discordapp.com/api/webhooks/")
        ):
            raise HTTPException(status_code=422, detail="Enter a valid Discord webhook URL")
        updates["discord_webhook_url"] = webhook
    if updates:
        db.set_settings(updates)
        if any(key in updates for key in ("min_profit", "min_roi", "min_confidence", "ebay_fvf",
                                          "resale_channel", "resale_shipping", "packaging_cost",
                                          "other_costs", "asking_to_sold_factor",
                                          "resale_strategy", "min_roi_high_value",
                                          "high_value_threshold", "history_reuse_days")):
            await request.app.state.scanner._run_refresh(force=True)
    return await get_settings(request)


class EbayCredentialsUpdate(BaseModel):
    client_id: str = Field(min_length=8, max_length=200)
    client_secret: str = Field(min_length=8, max_length=200)
    environment: str = Field(default="production", max_length=20)


def _reset_ebay_collector(request: Request) -> None:
    collector = _scanner(request).collectors.get("ebay")
    if collector is not None:
        collector._token = None
        collector._token_expires = 0.0
        collector._token_fingerprint = None
        collector._quota_pause_until = 0.0
        collector._quota_reason = None


@router.post("/settings/ebay")
async def save_ebay_credentials(request: Request, body: EbayCredentialsUpdate) -> dict[str, Any]:
    try:
        status = credential_store.save_ebay(body.client_id, body.client_secret, body.environment)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _reset_ebay_collector(request)
    return {"credentials": status, "settings": await get_settings(request)}


@router.post("/settings/ebay/test")
async def test_ebay_connection(request: Request) -> dict[str, Any]:
    if not credential_store.ebay_configured:
        raise HTTPException(status_code=422, detail="Save eBay developer credentials first")
    collector = _scanner(request).collectors.get("ebay")
    if collector is None:
        raise HTTPException(status_code=404, detail="eBay collector is not available")
    result = await collector.probe()
    return {
        **result,
        "credentials": credential_store.status(),
        "tested_at": iso(),
    }


@router.delete("/settings/ebay")
async def remove_ebay_credentials(request: Request) -> dict[str, Any]:
    status = credential_store.clear_ebay()
    _reset_ebay_collector(request)
    return {"credentials": status, "settings": await get_settings(request)}


class ScanRequest(BaseModel):
    marketplace: str | None = None


@router.post("/scan")
async def trigger_scan(request: Request, body: ScanRequest | None = None) -> dict[str, Any]:
    scanner = _scanner(request)
    marketplace = (body.marketplace if body else None) or None
    if marketplace and marketplace not in scanner.collectors:
        raise HTTPException(status_code=404, detail="Unknown marketplace")
    try:
        accepted = await scanner.trigger(marketplace)
    except (ScanConfigurationRequired, ScannerMaintenance) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if accepted:
        message = "Scan started"
    elif marketplace and not scanner.scan_config.marketplace_enabled(marketplace):
        message = "That marketplace is disabled in Scan configuration"
    else:
        message = "No scan started — check the saved categories and marketplace toggles"
    return {
        "accepted": accepted,
        "message": message,
        "time": iso(),
    }


@router.get("/sources")
async def sources(request: Request) -> dict[str, Any]:
    return _scanner(request).health_snapshot()


@router.get("/notifications")
async def notifications(request: Request, limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    rows = _db(request).query(
        "SELECT n.*, l.title, l.url FROM notifications n LEFT JOIN listings l ON l.id=n.listing_id "
        "ORDER BY n.created_at DESC LIMIT ?",
        (limit,),
    )
    return {"notifications": rows}


class FlipStart(BaseModel):
    listing_id: int | None = None
    title: str | None = None
    product_key: str | None = None
    kind: str | None = None
    marketplace: str | None = None
    condition: str | None = None
    purchase_price: float | None = None
    purchase_shipping: float | None = None
    preparation_cost: float | None = None
    ask_price: float | None = None
    predicted_resale: float | None = None
    predicted_profit: float | None = None
    channel: str | None = None


class FlipUpdate(BaseModel):
    purchase_price: float | None = None
    purchase_shipping: float | None = None
    preparation_cost: float | None = None
    other_costs: float | None = None
    ask_price: float | None = None
    final_price: float | None = None
    fees: float | None = None
    postage: float | None = None
    channel: str | None = None
    status: str | None = None
    condition_norm: str | None = None
    sold_at: str | None = None
    feedback: int | None = None
    feedback_note: str | None = None


class FlipPrice(BaseModel):
    price: float


@router.get("/flips")
async def list_flips(
    request: Request,
    status: str = "all",
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    book = FlipBook(_db(request))
    return {
        "flips": book.list(status, limit),
        "summary": book.summary(),
        "generated_at": iso(),
    }


@router.get("/flips/summary")
async def flips_summary(request: Request) -> dict[str, Any]:
    return FlipBook(_db(request)).summary()


@router.post("/flips")
async def start_flip(request: Request, body: FlipStart) -> dict[str, Any]:
    book = FlipBook(_db(request))
    try:
        if body.listing_id is not None:
            flip = book.start_from_listing(
                body.listing_id,
                purchase_price=body.purchase_price,
                purchase_shipping=body.purchase_shipping,
                preparation_cost=body.preparation_cost,
                ask_price=body.ask_price,
                channel=body.channel,
            )
        else:
            flip = book.start_manual(body.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"flip": flip, "summary": book.summary()}


@router.get("/flips/{flip_id}")
async def get_flip(request: Request, flip_id: int) -> dict[str, Any]:
    flip = FlipBook(_db(request)).get(flip_id)
    if not flip:
        raise HTTPException(status_code=404, detail="Flip not found")
    return flip


@router.patch("/flips/{flip_id}")
async def update_flip(request: Request, flip_id: int, body: FlipUpdate) -> dict[str, Any]:
    book = FlipBook(_db(request))
    if body.status is not None and body.status not in {"held", "listed", "sold", "unsold"}:
        raise HTTPException(status_code=422, detail="Unknown flip status")
    if body.feedback is not None and body.feedback not in {-1, 0, 1}:
        raise HTTPException(status_code=422, detail="Feedback must be -1, 0 or 1")
    try:
        flip = book.update(flip_id, body.model_dump(exclude_unset=True))
    except KeyError:
        raise HTTPException(status_code=404, detail="Flip not found")
    return {"flip": flip, "summary": book.summary()}


@router.post("/flips/{flip_id}/price")
async def flip_price(request: Request, flip_id: int, body: FlipPrice) -> dict[str, Any]:
    try:
        flip = FlipBook(_db(request)).change_price(flip_id, body.price)
    except KeyError:
        raise HTTPException(status_code=404, detail="Flip not found")
    return {"flip": flip}


@router.get("/products/{product_key:path}")
async def product_price(request: Request, product_key: str) -> dict[str, Any]:
    """Permanent price memory and independent CeX reference for one product."""
    db = _db(request)
    from .pricing import PricingDatabase

    key = product_key.strip().lower()
    detail = PricingDatabase(db).detail(key)
    quote = db.query_one(
        "SELECT * FROM external_quotes WHERE product_key=? AND source='cex'",
        (key,),
    )
    cex_history = db.query(
        """
        SELECT matched_name, grade, sell_price, cash_price, voucher_price,
               condition, observed_at
        FROM cex_price_history WHERE product_key=?
        ORDER BY observed_at DESC LIMIT 20
        """,
        (key,),
    )
    return {
        "product_key": key,
        "pricing": detail,
        "cex": dict(quote) if quote else None,
        "cex_history": cex_history,
    }


def _unused(*_: Any) -> None:
    # keep imports referenced for static analysers
    _ = (parse_iso, utcnow)
