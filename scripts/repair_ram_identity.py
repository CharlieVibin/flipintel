"""Repair stored RAM identities and remove auctions from Human Review.

This is deliberately a targeted reconciliation, not a full-history rebuild:

* re-identifies rows already classified as RAM with the corrected ECC, SO-DIMM
  and DDR-speed rules;
* records an identity revision when the product key moves;
* invalidates cached valuations for the superseded keys and revalues only the
  affected listings;
* clears the review flag from live auctions while keeping them in the Auctions
  workspace.

Raw listings, market observations and historical snapshots are never deleted.
Run it as often as needed; rows that are already correct are left untouched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Database  # noqa: E402
from app.identification import IDENTIFICATION_VERSION, identify_listing  # noqa: E402
from app.utils import iso, json_dumps  # noqa: E402
from app.valuation import ValuationEngine  # noqa: E402
from app.visibility import leaf_for_row  # noqa: E402


def repair_ram(db: Database) -> dict[str, int]:
    rows = db.query(
        """
        SELECT id, title, description, condition_raw, condition_norm, category,
               product_key, product_name, identification_confidence, is_faulty,
               is_wanted, is_bundle, listing_kind, flags, attrs, identity_level,
               identity_basis, scan_leaf, sale_role
        FROM listings
        WHERE category='ram' OR product_key LIKE 'ram:%'
        ORDER BY id
        """
    )
    changed_ids: list[int] = []
    stale_keys: set[str] = set()
    key_changes = 0
    speed_recovered = 0
    ecc_corrected = 0
    condition_corrected = 0
    for row in rows:
        ident = identify_listing(row["title"] or "", row["description"] or "")
        old_attrs = {}
        try:
            old_attrs = json.loads(row["attrs"] or "{}")
        except (TypeError, ValueError):
            pass
        new_flags = json_dumps(ident.flags)
        new_attrs = json_dumps(ident.attrs)
        scan_leaf = leaf_for_row(
            {
                **row,
                "category": ident.category,
                "product_key": ident.product_key,
                "flags": new_flags,
                "attrs": new_attrs,
            }
        )
        old_key = row["product_key"]
        fields_changed = not (
            row["category"] == ident.category
            and old_key == ident.product_key
            and row["product_name"] == ident.product_name
            and abs(float(row["identification_confidence"] or 0) - ident.confidence) < 0.001
            and int(row["is_faulty"] or 0) == int(ident.is_faulty)
            and int(row["is_wanted"] or 0) == int(ident.is_wanted)
            and int(row["is_bundle"] or 0) == int(ident.is_bundle)
            and row["listing_kind"] == ident.kind
            and row["flags"] == new_flags
            and row["attrs"] == new_attrs
            and row.get("identity_level") == ident.identity_level
            and row.get("identity_basis") == json_dumps(ident.identity_basis)
            and row.get("scan_leaf") == scan_leaf
            and row.get("sale_role") == ident.sale_role
        )
        if not fields_changed:
            continue
        if old_key != ident.product_key:
            key_changes += 1
            if old_key:
                stale_keys.add(str(old_key))
            db.execute(
                """
                INSERT INTO identity_revisions(
                    listing_id, old_product_key, new_product_key, old_kind, new_kind,
                    was_deal, old_resale, old_evidence_level, created_at
                )
                SELECT ?, ?, ?, ?, ?, d.is_deal, d.expected_resale, d.evidence_level, ?
                FROM listings l LEFT JOIN deals d ON d.listing_id = l.id
                WHERE l.id = ?
                """,
                (
                    row["id"], old_key, ident.product_key, row["listing_kind"],
                    ident.kind, iso(), row["id"],
                ),
            )
        if not old_attrs.get("speed_mhz") and ident.attrs.get("speed_mhz"):
            speed_recovered += 1
        old_ecc = old_attrs.get("ecc")
        if old_ecc is None:
            old_ecc = str(old_key or "").endswith(":ecc") or old_attrs.get("form_factor") == "ecc"
        if bool(old_ecc) != bool(ident.attrs.get("ecc")):
            ecc_corrected += 1
        condition = row.get("condition_norm") or "unknown"
        if ident.attrs.get("functionality") == "untested" and condition == "faulty":
            condition = "unknown"
            condition_corrected += 1
        elif not ident.is_faulty and condition == "faulty":
            condition = "used"
            condition_corrected += 1
        db.execute(
            """
            UPDATE listings SET
                category=?, product_key=?, product_name=?, identification_confidence=?,
                is_faulty=?, is_wanted=?, is_bundle=?, listing_kind=?, flags=?, attrs=?,
                identity_level=?, identity_basis=?, scan_leaf=?, sale_role=?,
                condition_norm=?, identity_version=?
            WHERE id=?
            """,
            (
                ident.category, ident.product_key, ident.product_name, ident.confidence,
                int(ident.is_faulty), int(ident.is_wanted), int(ident.is_bundle),
                ident.kind, new_flags, new_attrs, ident.identity_level,
                json_dumps(ident.identity_basis), scan_leaf, ident.sale_role,
                condition, IDENTIFICATION_VERSION, row["id"],
            ),
        )
        changed_ids.append(int(row["id"]))

    invalidated = 0
    if stale_keys:
        invalidated = ValuationEngine(db).pricing.invalidate(
            sorted(stale_keys), "RAM identity corrected (ECC/form factor/speed)"
        )
    valued = {"evaluated": 0, "deals": 0, "review": 0, "snapshots": 0}
    if changed_ids:
        valued = ValuationEngine(db).revalue_ids(changed_ids)
    return {
        "ram_rows": len(rows),
        "changed": len(changed_ids),
        "key_changes": key_changes,
        "speed_recovered": speed_recovered,
        "ecc_corrected": ecc_corrected,
        "condition_corrected": condition_corrected,
        "invalidated_snapshots": invalidated,
        **{f"revalue_{key}": value for key, value in valued.items()},
    }


def clear_auction_reviews(db: Database) -> dict[str, int]:
    before = int(
        db.scalar(
            "SELECT COUNT(*) FROM deals d JOIN listings l ON l.id=d.listing_id "
            "WHERE d.needs_review=1 AND l.is_auction=1"
        )
        or 0
    )
    cursor = db.execute(
        """
        UPDATE deals SET needs_review=0, review_reason=NULL
        WHERE needs_review=1
          AND listing_id IN (SELECT id FROM listings WHERE is_auction=1)
        """
    )
    after = int(
        db.scalar(
            "SELECT COUNT(*) FROM deals d JOIN listings l ON l.id=d.listing_id "
            "WHERE d.needs_review=1 AND l.is_auction=1"
        )
        or 0
    )
    return {"auction_reviews_before": before, "cleared": max(0, cursor.rowcount or 0), "after": after}


def main() -> None:
    db = Database()
    print("RAM identity repair")
    print(json.dumps(repair_ram(db), indent=2))
    print("Auction review repair")
    print(json.dumps(clear_auction_reviews(db), indent=2))
    db.close()


if __name__ == "__main__":
    main()
