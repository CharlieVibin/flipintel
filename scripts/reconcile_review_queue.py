"""Targeted review-queue reconciliation.

Phase 6 corrected the identification rules for the whole history. A few rules
were still wrong in obvious ways (a cooler sold "no CPU" became the processor;
a 1TB-128TB variation listing became a 1TB drive; the same seller's relists each
became a review card). This script repairs only the records that are actually
visible as a recommendation or a pending review, plus the duplicates around
them. It never deletes listings, observations, snapshots or Flips.

The pass is idempotent: identity is rewritten from the current classifier when
it differs, duplicates are linked by :mod:`app.dedup`, and the valuation engine
recomputes eligibility for exactly the rows whose stored fingerprint changed.

Usage::

    .venv/Scripts/python.exe scripts/reconcile_review_queue.py [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Database
from app.dedup import mark_duplicates
from app.identification import IDENTIFICATION_VERSION, identify_listing
from app.scanner import identity_hash
from app.scan_config import scan_leaf_for
from app.utils import hours_ago, iso, json_dumps
from app.valuation import ValuationEngine

VISIBLE = (
    "l.status = 'active' AND (d.is_deal = 1 OR d.needs_review = 1 "
    "OR d.eligibility IS NULL)"
)


def snapshot(db: Database) -> dict:
    def one(sql: str, params: tuple = ()) -> int:
        return int(db.scalar(sql, params) or 0)

    return {
        "generated_at": iso(),
        "recommended": one("SELECT COUNT(*) FROM deals WHERE is_deal=1"),
        "pending_review": one(
            "SELECT COUNT(*) FROM deals d JOIN listings l ON l.id=d.listing_id "
            "WHERE d.needs_review=1 AND d.eligibility='needs_verification' "
            "AND l.status='active' AND NOT EXISTS "
            "(SELECT 1 FROM review_decisions r WHERE r.listing_id=l.id)"
        ),
        "needs_verification_rows": one(
            "SELECT COUNT(*) FROM deals WHERE eligibility='needs_verification'"
        ),
        "excluded": one("SELECT COUNT(*) FROM deals WHERE eligibility='excluded'"),
        "duplicates": one(
            "SELECT COUNT(*) FROM listings WHERE status='active' "
            "AND duplicate_of IS NOT NULL"
        ),
        "cooler_cpu_rows": one(
            "SELECT COUNT(*) FROM listings WHERE product_key LIKE 'cpu:%' "
            "AND (title LIKE '%cooler%' OR title LIKE '%NO CPU%')"
        ),
        "capacity_range_rows": one(
            "SELECT COUNT(*) FROM listings WHERE title LIKE '%-%TB %' "
            "AND title LIKE '%Portable External SSD%' AND product_key IS NOT NULL"
        ),
    }


def reconcile(db: Database) -> dict:
    before = snapshot(db)
    rows = db.query(
        f"""
        SELECT l.*, COALESCE(r.decision, '') AS review_decision
        FROM deals d JOIN listings l ON l.id = d.listing_id
        LEFT JOIN review_decisions r ON r.listing_id = l.id
        WHERE {VISIBLE}
        """
    )
    pattern_sql = """
        l.status='active' AND (
            lower(l.title) LIKE '%cooler%' OR lower(l.title) LIKE '%fan only%'
            OR lower(l.title) LIKE '%no cpu%' OR lower(l.title) LIKE '%cpu only%'
            OR (l.title LIKE '%-%TB %' AND lower(l.title) LIKE '%ssd%')
            OR lower(l.title) LIKE '%unopened box%'
        )
    """
    visible_ids = {int(row["id"]) for row in rows}
    rows = list(rows) + list(db.query(
        f"""
        SELECT l.*, COALESCE(r.decision, '') AS review_decision
        FROM listings l
        LEFT JOIN review_decisions r ON r.listing_id = l.id
        WHERE {pattern_sql}
        """
    ))
    seen_ids: set[int] = set()
    changed = 0
    target_ids: list[int] = []
    reason_counts: dict[str, int] = {}
    examples: list[dict] = []
    for row in rows:
        listing_id = int(row["id"])
        visible = listing_id in visible_ids
        if listing_id in seen_ids:
            continue
        seen_ids.add(listing_id)
        if row.get("review_decision") == "corrected":
            if visible:
                target_ids.append(listing_id)
            continue
        title = row.get("title") or ""
        description = row.get("description") or ""
        condition_raw = row.get("condition_raw") or ""
        ident = identify_listing(title, f"{description} {condition_raw}", row.get("marketplace") or "")
        source_category = db.scalar(
            "SELECT category_id FROM listing_categories WHERE listing_id=? "
            "ORDER BY last_seen_at DESC LIMIT 1",
            (listing_id,),
        )
        scan_leaf = scan_leaf_for(ident, title, source_category, row.get("marketplace") or None)
        previous = (
            row.get("product_key"), row.get("listing_kind"), row.get("flags"),
            row.get("sale_role"),
        )
        current = (
            ident.product_key, ident.kind, json_dumps(ident.flags), ident.sale_role,
        )
        if previous == current and row.get("scan_leaf") == scan_leaf:
            if visible:
                target_ids.append(listing_id)
            continue
        db.execute(
            """
            UPDATE listings SET
                listing_kind=?, category=?, product_key=?, product_name=?,
                identification_confidence=?, is_faulty=?, is_wanted=?, is_bundle=?,
                flags=?, attrs=?, identity_level=?, identity_basis=?, sale_role=?,
                scan_leaf=?, identity_version=?, identity_hash=?, updated_at=?
            WHERE id=?
            """,
            (
                ident.kind, ident.category, ident.product_key, ident.product_name,
                ident.confidence, int(ident.is_faulty), int(ident.is_wanted),
                int(ident.is_bundle), json_dumps(ident.flags), json_dumps(ident.attrs),
                ident.identity_level, json_dumps(ident.identity_basis), ident.sale_role,
                scan_leaf, IDENTIFICATION_VERSION,
                identity_hash(title, description, condition_raw), iso(), listing_id,
            ),
        )
        changed += 1
        target_ids.append(listing_id)
        if row.get("product_key") != ident.product_key:
            reason_counts["product_key_reassigned"] = (
                reason_counts.get("product_key_reassigned", 0) + 1
            )
        flags = set(ident.flags)
        for flag in ("capacity_range", "unbranded_storage", "parts_only", "accessory_or_box"):
            if flag in flags:
                reason_counts[flag] = reason_counts.get(flag, 0) + 1
        if len(examples) < 25 and previous[0] != ident.product_key:
            examples.append(
                {
                    "id": listing_id,
                    "title": title,
                    "old_key": previous[0],
                    "new_key": ident.product_key,
                    "sale_role": ident.sale_role,
                    "flags": ident.flags,
                }
            )

    duplicates = mark_duplicates(db)
    # A newly linked duplicate may itself need a state update, so value every
    # examined row plus the relists just linked. This is a bounded, explicit
    # set: it never scans or resumes the full 113k-row refresh.
    for row in db.query(
        "SELECT id FROM listings WHERE duplicate_of IS NOT NULL "
        "AND status='active' AND last_seen >= ?",
        (hours_ago(24 * 30),),
    ):
        target_ids.append(int(row["id"]))
    refreshed = ValuationEngine(db).revalue_ids(target_ids)
    after = snapshot(db)
    return {
        "before": before,
        "after": after,
        "rows_examined": len(rows),
        "rows_reidentified": changed,
        "duplicates_marked": duplicates,
        "reasons": reason_counts,
        "examples": examples,
        "valuation": refreshed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/rigradar.db")
    parser.add_argument("--out", default="data/review_reconcile.json")
    args = parser.parse_args()
    db = Database(args.db)
    try:
        report = reconcile(db)
    finally:
        db.close()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "examples"}, indent=2))
    print(f"examples: {len(report['examples'])} -> {out}")


if __name__ == "__main__":
    main()
