"""HTTP API used by the dashboard."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .config import CATEGORY_LABELS, MARKETPLACE_LABELS, settings
from .db import Database
from .scanner import Scanner
from .utils import age_hours, hours_ago, iso, parse_iso, utcnow
from .valuation import ValuationConfig

router = APIRouter(prefix="/api")

SORT_SQL = {
    "score": "d.score DESC, d.net_profit DESC",
    "newest": "l.first_seen DESC",
    "profit": "d.net_profit DESC, d.score DESC",
    "roi": "d.roi DESC, d.net_profit DESC",
    "confidence": "d.confidence DESC, d.score DESC",
    "price": "l.price ASC",
}


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


def _listing_row(row: dict[str, Any]) -> dict[str, Any]:
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
        "is_faulty": bool(row.get("is_faulty")),
        "is_wanted": bool(row.get("is_wanted")),
        "is_auction": bool(row.get("is_auction")),
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
        "sample_size": row.get("sample_size") or 0,
        "valuation_method": row.get("valuation_method"),
        "purchase_total": round(purchase_total, 2),
        "net_profit": round(float(row["net_profit"]), 2) if row.get("net_profit") is not None else None,
        "roi": row.get("roi"),
        "score": row.get("score") or 0,
        "is_deal": bool(row.get("is_deal")),
        "reasons": _parse_json(row.get("reasons"), []),
        "computed_at": row.get("computed_at"),
        "saved": bool(row.get("saved")),
        "dismissed": bool(row.get("dismissed")),
        "discount_pct": (
            round((float(expected) - purchase_total) / float(expected), 4)
            if expected and float(expected) > 0 else None
        ),
    }


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    db = _db(request)
    scanner = _scanner(request)
    counts = {
        "listings": db.scalar("SELECT COUNT(*) FROM listings") or 0,
        "active_listings": db.scalar("SELECT COUNT(*) FROM listings WHERE status='active'") or 0,
        "deals": db.scalar("SELECT COUNT(*) FROM deals d JOIN listings l ON l.id=d.listing_id "
                           "WHERE d.is_deal=1 AND l.status='active'") or 0,
    }
    last_scan = db.scalar("SELECT MAX(last_success_at) FROM marketplace_status")
    return {
        "status": "ok",
        "time": iso(),
        "version": "1.0.0",
        "scanner": scanner.health_snapshot(),
        "counts": counts,
        "last_successful_scan": last_scan,
        "ebay_configured": settings.ebay_configured,
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
    base = (
        "FROM deals d JOIN listings l ON l.id = d.listing_id "
        "WHERE d.is_deal = 1 AND l.status = 'active' "
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
        """
    ) or {}
    new_24h = db.scalar(
        f"SELECT COUNT(*) {base} AND l.first_seen >= ?", (hours_ago(24),)
    ) or 0
    best = db.query_one(
        f"""
        SELECT l.id, l.title, l.price, l.marketplace, l.image_url, l.category,
               d.expected_resale, d.net_profit, d.roi, d.score, d.confidence
        {base}
        ORDER BY d.score DESC LIMIT 1
        """
    )
    categories = db.query(
        f"""
        SELECT l.category, COUNT(*) AS count
        {base} AND l.category IS NOT NULL
        GROUP BY l.category ORDER BY count DESC
        """
    )
    marketplaces = db.query(
        f"""
        SELECT l.marketplace, COUNT(*) AS count
        {base}
        GROUP BY l.marketplace ORDER BY count DESC
        """
    )
    last_scans = db.query(
        "SELECT marketplace, state, last_success_at, last_error, next_scan_at, "
        "consecutive_failures FROM marketplace_status"
    )
    return {
        "deals": active.get("deals", 0),
        "potential_profit": round(float(active.get("potential_profit") or 0), 2),
        "avg_roi": round(float(active.get("avg_roi") or 0), 4),
        "avg_discount": round(float(active.get("avg_discount") or 0), 4),
        "best_profit": round(float(active.get("best_profit") or 0), 2),
        "avg_confidence": round(float(active.get("avg_confidence") or 0), 4),
        "new_deals_24h": new_24h,
        "best": best,
        "categories": categories,
        "marketplaces": marketplaces,
        "sources": last_scans,
        "active_listings": db.scalar("SELECT COUNT(*) FROM listings WHERE status='active'") or 0,
        "tracked_models": db.scalar(
            "SELECT COUNT(DISTINCT product_key) FROM listings WHERE status='active' AND product_key IS NOT NULL"
        ) or 0,
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
    min_profit: float = 0,
    min_roi: float = 0,
    min_confidence: float = 0,
    max_age_hours: float = 0,
    sort: str = "score",
    q: str | None = None,
    saved_only: bool = False,
    archived: bool = False,
    page: int = Query(1, ge=1),
    limit: int = Query(48, ge=1, le=120),
) -> dict[str, Any]:
    db = _db(request)
    where = ["l.status = 'active'"]
    params: list[Any] = []
    if not saved_only and not archived:
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
    order = SORT_SQL.get(sort, SORT_SQL["score"])
    where_sql = " AND ".join(where)
    joins = (
        "FROM deals d JOIN listings l ON l.id = d.listing_id "
        "LEFT JOIN saved_deals s ON s.listing_id = l.id "
        "LEFT JOIN dismissed_deals x ON x.listing_id = l.id"
    )
    total = db.scalar(f"SELECT COUNT(*) {joins} WHERE {where_sql}", params) or 0
    rows = db.query(
        f"""
        SELECT l.*, s.listing_id IS NOT NULL AS saved, x.listing_id IS NOT NULL AS dismissed,
               d.expected_resale, d.resale_low, d.resale_high, d.confidence, d.sample_size,
               d.valuation_method, d.purchase_total, d.net_profit, d.roi, d.score, d.is_deal,
               d.reasons, d.computed_at
        {joins}
        WHERE {where_sql}
        ORDER BY {order}
        LIMIT ? OFFSET ?
        """,
        params + [limit, (page - 1) * limit],
    )
    return {
        "deals": [_deal_row(row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
        "generated_at": iso(),
        "thresholds": {
            "min_profit": db.setting_float("min_profit", 20),
            "min_roi": db.setting_float("min_roi", 0.12),
            "min_confidence": db.setting_float("min_confidence", 0.42),
        },
    }


@router.get("/deals/{listing_id}")
async def deal_detail(request: Request, listing_id: int) -> dict[str, Any]:
    db = _db(request)
    row = db.query_one(
        """
        SELECT l.*, d.*, s.listing_id IS NOT NULL AS saved,
               x.listing_id IS NOT NULL AS dismissed
        FROM listings l
        LEFT JOIN deals d ON d.listing_id = l.id
        LEFT JOIN saved_deals s ON s.listing_id = l.id
        LEFT JOIN dismissed_deals x ON x.listing_id = l.id
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


class SettingsUpdate(BaseModel):
    min_profit: float | None = Field(default=None, ge=0, le=10000)
    min_roi: float | None = Field(default=None, ge=0, le=5)
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    ebay_fvf: float | None = Field(default=None, ge=0, le=0.5)
    resale_shipping: float | None = Field(default=None, ge=0, le=200)
    packaging_cost: float | None = Field(default=None, ge=0, le=200)
    vinted_shipping: float | None = Field(default=None, ge=0, le=200)
    asking_to_sold_factor: float | None = Field(default=None, ge=0.4, le=1.2)
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
            "resale_shipping": num("resale_shipping", 4.5),
            "packaging_cost": num("packaging_cost", 1.0),
            "vinted_shipping": num("vinted_shipping", 3.49),
            "asking_to_sold_factor": num("asking_to_sold_factor", 0.9),
        },
        "scanning": {
            "scan_interval_vinted": int(num("scan_interval_vinted", 240)),
            "scan_interval_gumtree": int(num("scan_interval_gumtree", 300)),
            "scan_interval_ebay": int(num("scan_interval_ebay", 300)),
            "queries_per_cycle": int(num("queries_per_cycle", 12)),
            "enabled_vinted": boolean("enabled_vinted", True),
            "enabled_gumtree": boolean("enabled_gumtree", True),
            "enabled_ebay": boolean("enabled_ebay", True),
        },
        "notifications": {
            "enabled": boolean("notifications_enabled", False),
            "webhook_configured": bool(values.get("discord_webhook_url")),
        },
        "environment": {
            "ebay_configured": settings.ebay_configured,
            "facebook_available": False,
            "cex_enabled": settings.cex_enabled,
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
        "vinted_shipping": "vinted_shipping",
        "asking_to_sold_factor": "asking_to_sold_factor",
        "scan_interval_vinted": "scan_interval_vinted",
        "scan_interval_gumtree": "scan_interval_gumtree",
        "scan_interval_ebay": "scan_interval_ebay",
        "queries_per_cycle": "queries_per_cycle",
    }
    for field, key in mapping.items():
        value = getattr(body, field)
        if value is not None:
            updates[key] = str(int(value) if key.startswith("scan_interval") or key == "queries_per_cycle" else value)
    for field in ("notifications_enabled", "enabled_vinted", "enabled_gumtree", "enabled_ebay"):
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
                                          "resale_shipping", "packaging_cost", "asking_to_sold_factor")):
            await request.app.state.scanner._run_refresh(force=True)
    return await get_settings(request)


class ScanRequest(BaseModel):
    marketplace: str | None = None


@router.post("/scan")
async def trigger_scan(request: Request, body: ScanRequest | None = None) -> dict[str, Any]:
    scanner = _scanner(request)
    marketplace = (body.marketplace if body else None) or None
    if marketplace and marketplace not in scanner.collectors:
        raise HTTPException(status_code=404, detail="Unknown marketplace")
    accepted = await scanner.trigger(marketplace)
    return {
        "accepted": accepted,
        "message": "Scan started" if accepted else "A scan is already running for that source",
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


def _unused(*_: Any) -> None:
    # keep imports referenced for static analysers
    _ = (parse_iso, utcnow)
