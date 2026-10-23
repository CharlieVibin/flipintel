"""Read-only audit of the FlipIntel listing pipeline.

The audit answers four questions with the collected database:

* where listings are discovered and how fresh/duplicated they are;
* which listings get an exact product identity;
* which identified listings get a valuation;
* why the remaining listings are not recommended deals.

It never writes to the database. Run it before and after a change and compare
the JSON snapshots::

    .venv/Scripts/python.exe scripts/audit_pipeline.py --output data/audit.json
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUPPORTED_KINDS = {
    "gpu", "cpu", "ram", "ssd", "motherboard", "pc", "laptop", "console",
    "monitor", "phone", "tablet", "networking",
}

ELECTRONICS_CUES = {
    "gpu": r"\b(?:rtx|gtx|rx|radeon|geforce|graphics\s+card|gpu)\b",
    "cpu": r"\b(?:i[3579][\s-]?\d{3,5}|ryzen\s*\d|xeon|threadripper)\b",
    "motherboard": r"\b(?:b\d{3}|x\d{3}|z\d{3}|a\d{3}|h\d{3}|motherboard|mobo|mainboard)\b",
    "console": r"\b(?:ps[1-5]|playstation|xbox|nintendo|switch|game\s*boy|gba|snes|sega|mega\s*drive|dreamcast|ps\s*vita|psp)\b",
    "tablet": r"\b(?:ipad|galaxy\s+tab|lenovo\s+(?:tab|yoga\s+tab)|fire\s+hd|kindle\s+fire|surface\s+(?:pro|go)|xiaomi\s+pad|redmi\s+pad|oneplus\s+pad)\b",
    "phone": r"\b(?:iphone|galaxy\s+[saz]|pixel|motorola|moto\s+[a-z]|oneplus|xiaomi|redmi|oppo|honor|realme|nokia|sony\s+xperia|nothing\s+phone)\b",
    "monitor": r"\bmonitor\b",
    "laptop": r"\b(?:laptop|notebook|macbook|chromebook|elitebook|probook|thinkpad|ideapad)\b",
}


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    return connection


def _flags(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


def _reasons(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _listing_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT l.id, l.marketplace, l.category, l.listing_kind, l.product_key,
               l.product_name, l.identification_confidence, l.is_faulty,
               l.is_wanted, l.is_bundle, l.flags, l.price, l.first_seen,
               l.last_seen, l.status, l.duplicate_of, l.search_query,
               l.title, l.description,
               d.listing_id AS deal_id, d.expected_resale, d.resale_low, d.resale_high,
               d.confidence, d.sample_size, d.net_profit, d.roi, d.is_deal,
               d.needs_review, d.review_reason, d.reasons, d.risk_score,
               d.risk_level, d.valuation_method
        FROM listings l
        LEFT JOIN deals d ON d.listing_id = l.id
        WHERE l.status = 'active'
        ORDER BY l.id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _settings(connection: sqlite3.Connection) -> dict[str, str]:
    try:
        return {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key, value FROM settings")
        }
    except sqlite3.OperationalError:
        return {}


def _sample(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "marketplace": row["marketplace"],
            "price": row["price"],
            "kind": row["listing_kind"],
            "product": row["product_name"],
            "title": (row["title"] or "")[:160],
        }
        for row in rows[:limit]
    ]


def _summarise(rows: list[dict[str, Any]], settings: dict[str, str]) -> dict[str, Any]:
    min_profit = float(settings.get("min_profit", 20.0))
    min_roi = float(settings.get("min_roi", 0.12))
    min_confidence = float(settings.get("min_confidence", 0.42))

    def count(predicate) -> int:
        return sum(1 for row in rows if predicate(row))

    stages = {
        "active_listings": len(rows),
        "exact_identity": count(lambda row: bool(row["product_key"])),
        "supported_kind": count(
            lambda row: bool(row["product_key"]) and row["listing_kind"] in SUPPORTED_KINDS
        ),
        "valued": count(lambda row: bool(row["expected_resale"])),
        "enough_comparables": count(
            lambda row: bool(row["expected_resale"]) and int(row["sample_size"] or 0) >= 4
        ),
        "not_in_review": count(
            lambda row: bool(row["expected_resale"])
            and int(row["sample_size"] or 0) >= 4
            and not row["needs_review"]
        ),
        "recommended_deals": count(lambda row: bool(row["is_deal"])),
        "review_queue": count(lambda row: bool(row["needs_review"])),
    }

    def primary_failure(row: dict[str, Any]) -> str:
        if row["is_deal"]:
            return "deal"
        if row["is_faulty"]:
            return "faulty_or_parts"
        if row["is_wanted"]:
            return "wanted_advertisement"
        if not row["product_key"]:
            return "no_exact_identity"
        if row["listing_kind"] not in SUPPORTED_KINDS:
            return "unsupported_kind"
        if not row["expected_resale"]:
            return "no_usable_valuation"
        if int(row["sample_size"] or 0) < 4:
            return "fewer_than_4_comparables"
        if row["needs_review"]:
            return "manual_review"
        if (row["identification_confidence"] or 0) < 0.55:
            return "identity_confidence_below_0.55"
        if row["is_bundle"]:
            return "bundle"
        if (row["confidence"] or 0) < min_confidence:
            return "valuation_confidence_below_threshold"
        if (row["net_profit"] or 0) < min_profit:
            return "profit_below_threshold"
        if (row["roi"] or 0) < min_roi:
            return "roi_below_threshold"
        return "other"

    failures = collections.Counter(primary_failure(row) for row in rows)
    failure_samples: dict[str, list[dict[str, Any]]] = {}
    for failure in failures:
        failure_samples[failure] = _sample(
            [row for row in rows if primary_failure(row) == failure]
        )

    reason_counts: collections.Counter[str] = collections.Counter()
    review_counts: collections.Counter[str] = collections.Counter()
    for row in rows:
        for reason in _reasons(row["reasons"]):
            reason_counts[reason] += 1
        for reason in str(row["review_reason"] or "").split("; "):
            if reason:
                review_counts[reason] += 1

    near_misses = [
        row for row in rows
        if not row["is_deal"]
        and row["expected_resale"]
        and (row["net_profit"] or 0) >= min_profit
        and (row["roi"] or 0) >= min_roi
    ]

    unidentified = [row for row in rows if not row["product_key"]]
    cue_counts: dict[str, int] = {}
    cue_samples: dict[str, list[dict[str, Any]]] = {}
    for label, pattern in ELECTRONICS_CUES.items():
        matched = [
            row for row in unidentified
            if re.search(pattern, row["title"] or "", re.I)
        ]
        cue_counts[label] = len(matched)
        cue_samples[label] = _sample(matched)

    flags = collections.Counter(
        flag for row in rows for flag in _flags(row["flags"])
    )

    return {
        "stages": stages,
        "primary_failure": dict(failures.most_common()),
        "failure_examples": failure_samples,
        "exact_reason_counts": dict(reason_counts.most_common(40)),
        "review_reason_counts": dict(review_counts.most_common(40)),
        "near_misses": {
            "count": len(near_misses),
            "examples": [
                {
                    **_sample([row], 1)[0],
                    "expected_resale": row["expected_resale"],
                    "net_profit": row["net_profit"],
                    "roi": row["roi"],
                    "confidence": row["confidence"],
                    "sample_size": row["sample_size"],
                    "review_reason": row["review_reason"],
                    "failure": primary_failure(row),
                }
                for row in near_misses[:12]
            ],
        },
        "unidentified_electronics_cues": cue_counts,
        "unidentified_cue_examples": cue_samples,
        "flag_counts": dict(flags.most_common()),
        "thresholds": {
            "min_profit": min_profit,
            "min_roi": min_roi,
            "min_confidence": min_confidence,
        },
    }


def _group(
    rows: list[dict[str, Any]], key: str
) -> dict[str, dict[str, int]]:
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        name = str(row.get(key) or "unknown")
        bucket = grouped.setdefault(
            name,
            {"active": 0, "exact_identity": 0, "valued": 0, "deals": 0, "review": 0},
        )
        bucket["active"] += 1
        bucket["exact_identity"] += int(bool(row["product_key"]))
        bucket["valued"] += int(bool(row["expected_resale"]))
        bucket["deals"] += int(bool(row["is_deal"]))
        bucket["review"] += int(bool(row["needs_review"]))
    return dict(sorted(grouped.items(), key=lambda item: item[1]["active"], reverse=True))


def _freshness(connection: sqlite3.Connection, now: datetime) -> dict[str, int]:
    rows = connection.execute(
        "SELECT last_seen, first_seen FROM listings WHERE status='active'"
    ).fetchall()
    buckets = collections.Counter()
    for row in rows:
        try:
            seen = datetime.fromisoformat(str(row["last_seen"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            buckets["unknown"] += 1
            continue
        age_hours = max(0.0, (now - seen).total_seconds() / 3600)
        if age_hours <= 24:
            buckets["last_24h"] += 1
        elif age_hours <= 24 * 7:
            buckets["last_7d"] += 1
        elif age_hours <= 24 * 14:
            buckets["last_14d"] += 1
        else:
            buckets["older"] += 1
    return dict(buckets)


def _duplicates(connection: sqlite3.Connection) -> dict[str, int]:
    total = connection.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    active = connection.execute(
        "SELECT COUNT(*) FROM listings WHERE status='active'"
    ).fetchone()[0]
    flagged = connection.execute(
        "SELECT COUNT(*) FROM listings WHERE duplicate_of IS NOT NULL"
    ).fetchone()[0]
    cross = connection.execute(
        """
        SELECT COUNT(*) FROM listings l JOIN listings o ON o.id=l.duplicate_of
        WHERE l.marketplace != o.marketplace
        """
    ).fetchone()[0]
    return {
        "rows": total,
        "active": active,
        "marked_duplicate": flagged,
        "cross_marketplace": cross,
    }


def _scan_efficiency(connection: sqlite3.Connection) -> dict[str, Any]:
    runs = connection.execute(
        """
        SELECT marketplace, started_at, status, items_found, items_new,
               duplicates, duration_ms, detail
        FROM scan_runs ORDER BY id DESC LIMIT 60
        """
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for row in runs:
        marketplace = str(row["marketplace"])
        bucket = grouped.setdefault(
            marketplace,
            {
                "runs": 0, "items_found": 0, "items_new": 0,
                "duplicates": 0, "duration_ms": 0, "supplement_items": 0,
                "categories": collections.Counter(),
            },
        )
        bucket["runs"] += 1
        bucket["items_found"] += int(row["items_found"] or 0)
        bucket["items_new"] += int(row["items_new"] or 0)
        bucket["duplicates"] += int(row["duplicates"] or 0)
        bucket["duration_ms"] += int(row["duration_ms"] or 0)
        try:
            detail = json.loads(row["detail"] or "{}")
        except (TypeError, ValueError):
            detail = {}
        supplement = detail.get("keyword_supplement") or {}
        bucket["supplement_items"] += int(supplement.get("items") or 0)
        for category in detail.get("categories") or []:
            bucket["categories"][str(category.get("category_id") or "")] += int(
                category.get("items") or 0
            )
    for bucket in grouped.values():
        bucket["categories"] = dict(bucket["categories"].most_common())
        bucket["avg_duration_ms"] = round(
            bucket["duration_ms"] / max(1, bucket["runs"])
        )
        bucket["new_rate"] = round(
            bucket["items_new"] / max(1, bucket["items_found"]), 3
        )
    category_scans = [
        dict(row) for row in connection.execute(
            """
            SELECT marketplace, category_id, label, backfill_done, coverage,
                   last_items, last_new, last_duplicates, new_rate, duplicate_rate,
                   interval_seconds, last_error
            FROM category_scans ORDER BY marketplace, label
            """
        )
    ]
    return {"recent_runs": grouped, "categories": category_scans}


def build_audit(path: Path) -> dict[str, Any]:
    connection = _connect(path)
    try:
        rows = _listing_rows(connection)
        settings = _settings(connection)
        now = datetime.now(timezone.utc)
        unique_products = connection.execute(
            "SELECT COUNT(DISTINCT product_key) FROM listings "
            "WHERE status='active' AND product_key IS NOT NULL"
        ).fetchone()[0]
        return {
            "generated_at": now.isoformat(),
            "database": str(path),
            "unique_products": int(unique_products),
            "summary": _summarise(rows, settings),
            "by_marketplace": _group(rows, "marketplace"),
            "by_category": _group(rows, "category"),
            "freshness": _freshness(connection, now),
            "duplicates": _duplicates(connection),
            "scan_efficiency": _scan_efficiency(connection),
        }
    finally:
        connection.close()


def _print_summary(audit: dict[str, Any]) -> None:
    stages = audit["summary"]["stages"]
    print("FlipIntel pipeline audit")
    print(f"  database: {audit['database']}")
    print(f"  generated: {audit['generated_at']}")
    print()
    print("Funnel")
    for name, value in stages.items():
        print(f"  {name:>34}: {value:>7,}")
    print()
    print("Primary failure reasons")
    for name, value in audit["summary"]["primary_failure"].items():
        print(f"  {name:>34}: {value:>7,}")
    print()
    print("Marketplaces")
    for name, values in audit["by_marketplace"].items():
        print(
            f"  {name:>12}: {values['active']:>7,} active, "
            f"{values['exact_identity']:>7,} identified, "
            f"{values['valued']:>7,} valued, {values['deals']:>4,} deals, "
            f"{values['review']:>5,} review"
        )
    print()
    print("Near misses (profit/ROI pass, another gate blocks)")
    print(f"  {audit['summary']['near_misses']['count']:,}")
    print()
    print("Unidentified titles with electronics cues")
    for name, value in audit["summary"]["unidentified_electronics_cues"].items():
        print(f"  {name:>34}: {value:>7,}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default="data/rigradar.db", help="SQLite database path"
    )
    parser.add_argument("--output", help="Write the full JSON audit to this path")
    args = parser.parse_args()
    path = Path(args.db)
    if not path.exists():
        raise SystemExit(f"database not found: {path}")
    audit = build_audit(path)
    _print_summary(audit)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(audit, indent=2), encoding="utf-8")
        print(f"\nJSON written to {output}")


if __name__ == "__main__":
    main()
