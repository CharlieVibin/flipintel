"""Persistent per-marketplace request accounting.

Marketplace APIs rarely expose a live remaining-quota figure. We therefore keep
our own daily request/error counters and attach provider quota headers when they
are present. Missing quota information is reported as ``unknown``; it is never
rendered as a fabricated number.
"""
from __future__ import annotations

from typing import Any

from .db import Database
from .utils import iso, utcnow


def record_api_usage(
    db: Database,
    marketplace: str,
    *,
    status: int | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
    quota_remaining: int | None = None,
    quota_limit: int | None = None,
    quota_reset_at: str | None = None,
) -> None:
    day = utcnow().date().isoformat()
    now = iso()
    success_at = now if status is not None and status < 400 and not error else None
    try:
        db.execute(
            """
            INSERT INTO api_usage_daily(
                marketplace, day, requests, errors, last_status, last_error,
                last_request_at, last_success_at, quota_remaining, quota_limit, quota_reset_at
            ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(marketplace, day) DO UPDATE SET
                requests = api_usage_daily.requests + 1,
                errors = api_usage_daily.errors + CASE WHEN excluded.errors > 0 THEN 1 ELSE 0 END,
                last_status = COALESCE(excluded.last_status, api_usage_daily.last_status),
                last_error = COALESCE(excluded.last_error, api_usage_daily.last_error),
                last_request_at = excluded.last_request_at,
                last_success_at = COALESCE(excluded.last_success_at, api_usage_daily.last_success_at),
                quota_remaining = COALESCE(excluded.quota_remaining, api_usage_daily.quota_remaining),
                quota_limit = COALESCE(excluded.quota_limit, api_usage_daily.quota_limit),
                quota_reset_at = COALESCE(excluded.quota_reset_at, api_usage_daily.quota_reset_at)
            """,
            (
                marketplace,
                day,
                1 if error else 0,
                status,
                (error or "")[:500] or None,
                now,
                success_at,
                quota_remaining,
                quota_limit,
                quota_reset_at,
            ),
        )
    except Exception:
        # Telemetry must never break a marketplace request.
        pass


def usage_snapshot(db: Database, marketplace: str, days: int = 7) -> dict[str, Any]:
    today = utcnow().date().isoformat()
    rows = db.query(
        "SELECT * FROM api_usage_daily WHERE marketplace=? ORDER BY day DESC LIMIT ?",
        (marketplace, max(1, days)),
    )
    today_row = next((row for row in rows if row["day"] == today), None)
    totals = db.query_one(
        "SELECT COALESCE(SUM(requests),0) AS requests, COALESCE(SUM(errors),0) AS errors "
        "FROM api_usage_daily WHERE marketplace=? AND day >= ?",
        (marketplace, _days_ago(max(1, days))),
    ) or {"requests": 0, "errors": 0}
    latest = next((row for row in rows if row.get("quota_remaining") is not None), None)
    latest_request = next((row for row in rows if row.get("last_request_at")), None)
    latest_success = next((row for row in rows if row.get("last_success_at")), None)
    quota: dict[str, Any] = {
        "state": "unknown",
        "remaining": None,
        "limit": None,
        "reset_at": None,
        "note": (
            "The provider does not expose remaining quota in a usable form. "
            "Local request counts are recorded instead."
        ),
    }
    if latest:
        remaining = int(latest["quota_remaining"])
        limit = int(latest["quota_limit"]) if latest.get("quota_limit") is not None else None
        state = "available"
        if remaining <= 0:
            state = "exhausted"
        elif limit and remaining <= max(5, int(limit * 0.05)):
            state = "low"
        quota = {
            "state": state,
            "remaining": remaining,
            "limit": limit,
            "reset_at": latest.get("quota_reset_at"),
            "note": "Provider quota headers supplied the remaining count.",
        }
    return {
        "marketplace": marketplace,
        "today": {
            "requests": today_row["requests"] if today_row else 0,
            "errors": today_row["errors"] if today_row else 0,
            "last_status": today_row.get("last_status") if today_row else None,
            "last_request_at": today_row.get("last_request_at") if today_row else None,
            "last_success_at": today_row.get("last_success_at") if today_row else None,
        },
        "last_request_at": latest_request.get("last_request_at") if latest_request else None,
        "last_success_at": latest_success.get("last_success_at") if latest_success else None,
        "window_days": max(1, days),
        "window_requests": int(totals.get("requests") or 0),
        "window_errors": int(totals.get("errors") or 0),
        "quota": quota,            "history": [
                {
                    "day": row["day"],
                    "requests": row["requests"],
                    "errors": row["errors"],
                    "last_status": row.get("last_status"),
                    "last_success_at": row.get("last_success_at"),
                }
                for row in reversed(rows)
            ],
    }


def _days_ago(days: int) -> str:
    from datetime import timedelta

    return (utcnow() - timedelta(days=days)).date().isoformat()
