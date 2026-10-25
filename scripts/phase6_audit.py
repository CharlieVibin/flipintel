"""Report the state of product identity, evidence and deal eligibility.

Run before the maintenance pass (``--out data/phase6_before.json``) and again
afterwards. The script only reads listings, observations, snapshots and deals;
it never repairs or deletes anything.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Database


def _one(db: Database, sql: str, params: tuple = ()) -> int:
    return int(db.scalar(sql, params) or 0)


def snapshot(db: Database) -> dict:
    latest = (
        "JOIN (SELECT product_key, MAX(id) AS id FROM valuation_snapshots "
        "WHERE invalidated_at IS NULL GROUP BY product_key) x "
        "ON x.id = valuation_snapshots.id"
    )
    keys = {
        row["product_key"]: row["resale_good"]
        for row in db.query(
            f"SELECT valuation_snapshots.product_key, valuation_snapshots.resale_good "
            f"FROM valuation_snapshots {latest}"
        )
    }
    # A capacity parsed from the fractional part of a decimal ("84TB" from
    # 3.84TB) is an objectively broken key.
    bad_capacity = sorted(
        key for key in keys
        if re.search(r":(?:[5-9]\d|1\d\d)tb\b", key, re.I)
    )
    ssd_over_200 = sorted(
        key for key in keys
        if key.startswith("ssd:") and float(keys[key] or 0) > 200
    )
    eligibility = {
        str(row["eligibility"] or "unset"): int(row["n"])
        for row in db.query(
            "SELECT eligibility, COUNT(*) AS n FROM deals GROUP BY eligibility"
        )
    }
    reasons = [
        dict(row)
        for row in db.query(
            """
            SELECT eligibility, eligibility_reason, COUNT(*) AS n
            FROM deals WHERE eligibility IS NOT NULL
            GROUP BY eligibility, eligibility_reason
            ORDER BY n DESC LIMIT 15
            """
        )
    ]
    role = {
        str(row["sale_role"] or "unset"): int(row["n"])
        for row in db.query(
            "SELECT sale_role, COUNT(*) AS n FROM listings GROUP BY sale_role"
        )
    }
    revisions = _one(db, "SELECT COUNT(*) FROM identity_revisions")
    revisions_promoted = _one(
        db, "SELECT COUNT(*) FROM identity_revisions WHERE was_deal=1"
    )
    revisions_with_key = _one(
        db,
        "SELECT COUNT(*) FROM identity_revisions "
        "WHERE old_product_key IS NOT NULL AND old_product_key != new_product_key",
    )
    review_pending = _one(
        db,
        "SELECT COUNT(*) FROM deals d JOIN listings l ON l.id=d.listing_id "
        "WHERE d.needs_review=1 AND COALESCE(d.eligibility,'') != 'excluded' "
        "AND l.status='active' AND NOT EXISTS "
        "(SELECT 1 FROM review_decisions r WHERE r.listing_id=l.id)",
    )
    examples = {
        key: value for key, value in sorted(keys.items())
        if any(
            token in key
            for token in (
                "256gb", "steam-deck", "rtx-4060", "rtx-4070", "z790",
                "h610", "3.84tb", "68tb", "84tb",
            )
        )
    }
    return {
        "generated_at": db.scalar("SELECT datetime('now')"),
        "listings": _one(db, "SELECT COUNT(*) FROM listings"),
        "active_listings": _one(
            db, "SELECT COUNT(*) FROM listings WHERE status='active'"
        ),
        "deals": _one(db, "SELECT COUNT(*) FROM deals"),
        "recommended": _one(db, "SELECT COUNT(*) FROM deals WHERE is_deal=1"),
        "needs_review": review_pending,
        "snapshots_total": _one(db, "SELECT COUNT(*) FROM valuation_snapshots"),
        "snapshots_live": _one(
            db, "SELECT COUNT(*) FROM valuation_snapshots WHERE invalidated_at IS NULL"
        ),
        "snapshots_invalidated": _one(
            db, "SELECT COUNT(*) FROM valuation_snapshots WHERE invalidated_at IS NOT NULL"
        ),
        "observations_total": _one(db, "SELECT COUNT(*) FROM market_observations"),
        "observations_live": _one(
            db, "SELECT COUNT(*) FROM market_observations WHERE invalidated_at IS NULL"
        ),
        "observations_invalidated": _one(
            db, "SELECT COUNT(*) FROM market_observations WHERE invalidated_at IS NOT NULL"
        ),
        "eligibility": eligibility,
        "eligibility_reasons": reasons,
        "sale_roles": role,
        "identity_revisions": revisions,
        "identity_revisions_promoted": revisions_promoted,
        "identity_revisions_with_old_key": revisions_with_key,
        "bad_capacity_keys": bad_capacity[:50],
        "bad_capacity_key_count": len(bad_capacity),
        "ssd_snapshots_over_200_count": len(ssd_over_200),
        "ssd_snapshots_over_200": ssd_over_200[:20],
        "valuation_examples": examples,
        "maintenance": {
            row["key"]: row["value"]
            for row in db.query("SELECT key, value FROM maintenance_state")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/rigradar.db")
    parser.add_argument("--out", default="data/phase6_audit.json")
    args = parser.parse_args()
    db = Database(args.db)
    try:
        report = snapshot(db)
    finally:
        db.close()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
