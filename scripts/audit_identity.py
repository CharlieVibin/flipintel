"""Audit and repair stored product identities.

The classifier is applied to every collected listing and compared with what is
stored. Report mode is strictly read-only. ``--apply`` writes the corrected
identity, invalidates cached valuations for superseded product keys and retires
market observations filed under an identity the listing no longer has. Raw rows
are never deleted.

    .venv/Scripts/python scripts/audit_identity.py
    .venv/Scripts/python scripts/audit_identity.py --apply --revalue
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.identification import identify_listing  # noqa: E402
from app.utils import iso  # noqa: E402

CONFIRMED_FAILURES = (
    "SanDisk microSD Express Card for Nintendo Switch 2",
    "Surface Pro 7+ 12.3",
    "Dell Pro Max Tower T2 FCT2250",
)


def _connect(path: Path, read_only: bool) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    else:
        conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params=()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def _bucket(value: float) -> str:
    if value >= 0.85:
        return "0.85+"
    if value >= 0.70:
        return "0.70-0.85"
    if value >= 0.55:
        return "0.55-0.70"
    if value > 0:
        return "0.01-0.55"
    return "0"


def audit_evidence(conn: sqlite3.Connection) -> dict:
    """Re-associate stored evidence with the identity its title supports.

    Raw observations are never deleted. An observation whose title no longer
    supports any exact variant is invalidated rather than left to feed a wrong
    product. Both the old and new keys are returned so their cached valuations
    are rebuilt.
    """
    now = iso()
    cache: dict[tuple[str, str], object] = {}
    observation_updates: list[tuple[str, str, int]] = []
    observation_invalidations: list[tuple[str, str, int]] = []
    comp_updates: list[tuple[str, str, int]] = []
    affected: set[str] = set()
    observation_reassigned = 0
    observation_unresolved = 0
    comp_reassigned = 0
    comp_unresolved = 0

    def identify(title: str, details: str = ""):
        cache_key = (title, details)
        cached = cache.get(cache_key)
        if cached is None:
            cached = identify_listing(title or "", details)
            cache[cache_key] = cached
        return cached

    # The observation table references listings through a text source_id. A
    # SQL join on ('listing:' || l.id) cannot use the primary-key index and turns
    # this audit into a full listings scan per observation, so resolve the
    # handful of fields once in memory instead.
    listing_details = {
        int(row[0]): (row[1], row[2], row[3])
        for row in conn.execute(
            "SELECT id, title, description, condition_raw FROM listings"
        )
    }
    for row in conn.execute(
        "SELECT id, title, source_id, product_key, kind "
        "FROM market_observations WHERE invalidated_at IS NULL"
    ):
        source_id = str(row["source_id"] or "")
        listing_id = None
        if source_id.startswith("listing:"):
            try:
                listing_id = int(source_id[8:])
            except ValueError:
                listing_id = None
        listing = listing_details.get(listing_id) if listing_id is not None else None
        listing_title = listing[0] if listing else None
        details = (
            f'{(listing[1] if listing else "") or ""} {(listing[2] if listing else "") or ""}'
            if listing else ""
        )
        ident = identify(listing_title or row["title"] or "", details)
        old_key = str(row["product_key"] or "")
        old_kind = str(row["kind"] or "")
        if ident.product_key and (not old_kind or old_kind == ident.kind):
            if ident.product_key != old_key or ident.kind != old_kind:
                observation_updates.append((ident.product_key, ident.kind, int(row["id"])))
                observation_reassigned += 1
                affected.add(old_key)
                affected.add(ident.product_key)
            continue
        observation_invalidations.append(
            (now, "variant identity could not be re-established", int(row["id"]))
        )
        observation_unresolved += 1
        affected.add(old_key)

    for row in conn.execute(
        "SELECT id, title, product_key, kind FROM market_comps"
    ):
        ident = identify(row["title"] or "")
        old_key = str(row["product_key"] or "")
        old_kind = str(row["kind"] or "")
        if ident.product_key and (not old_kind or old_kind == ident.kind):
            if ident.product_key != old_key or ident.kind != old_kind:
                comp_updates.append((ident.product_key, ident.kind, int(row["id"])))
                comp_reassigned += 1
                affected.add(old_key)
                affected.add(ident.product_key)
            continue
        comp_unresolved += 1
        affected.add(old_key)

    return {
        "observation_updates": observation_updates,
        "observation_invalidations": observation_invalidations,
        "comp_updates": comp_updates,
        "affected_keys": affected,
        "observation_reassigned": observation_reassigned,
        "observation_unresolved": observation_unresolved,
        "comp_reassigned": comp_reassigned,
        "comp_unresolved": comp_unresolved,
    }


def audit(conn: sqlite3.Connection, *, sample: int | None = None) -> dict:
    sql = (
        "SELECT id, title, description, condition_raw, category, product_key, "
        "product_name, listing_kind, identification_confidence, identity_level, "
        "flags, attrs FROM listings"
    )
    if sample:
        sql += f" LIMIT {int(sample)}"
    listings = _rows(conn, sql)
    transitions: Counter = Counter()
    kind_changes: Counter = Counter()
    changed = 0
    confidence_only = 0
    changed_rows: list[tuple] = []
    confidence_before: Counter = Counter()
    confidence_after: Counter = Counter()
    recovered_from_component = 0
    old_promoted: set[int] = set()
    new_valuable = 0
    identity_levels: Counter = Counter()
    for row in listings:
        confidence_before[_bucket(float(row["identification_confidence"] or 0))] += 1
        ident = identify_listing(
            row["title"] or "", f'{row["description"] or ""} {row["condition_raw"] or ""}'
        )
        confidence_after[_bucket(ident.confidence)] += 1
        identity_levels[ident.identity_level] += 1
        if ident.product_key:
            new_valuable += 1
        same = (
            row["product_key"] == ident.product_key
            and row["listing_kind"] == ident.kind
            and row["category"] == ident.category
            and abs(float(row["identification_confidence"] or 0) - ident.confidence) < 0.001
        )
        if same:
            continue
        changed += 1
        key_changed = row["product_key"] != ident.product_key
        kind_changed = row["listing_kind"] != ident.kind
        if not key_changed and not kind_changed:
            confidence_only += 1
        if key_changed or kind_changed:
            old_label = f'{row["listing_kind"]}:{row["product_key"]}'
            new_label = f"{ident.kind}:{ident.product_key}"
            transitions[(old_label, new_label)] += 1
        if kind_changed:
            kind_changes[(row["listing_kind"] or "none", ident.kind)] += 1
        if row["listing_kind"] in {"cpu", "gpu"} and ident.kind in {"pc", "laptop", "tablet"}:
            recovered_from_component += 1
        if row["product_key"] and row["product_key"] != ident.product_key:
            old_promoted.add(int(row["id"]))
        changed_rows.append(
            (
                ident.category,
                ident.product_key,
                ident.product_name,
                ident.confidence,
                int(ident.is_faulty),
                int(ident.is_wanted),
                int(ident.is_bundle),
                ident.kind,
                json.dumps(ident.flags),
                json.dumps(ident.attrs),
                ident.identity_level,
                json.dumps(ident.identity_basis),
                int(row["id"]),
            )
        )

    affected_keys = {
        old.split(":", 1)[1]
        for old, _new in transitions
        if ":" in old and not old.startswith(("none:", "other:")) and old.split(":", 1)[1] != "None"
    }
    # A changed attribute can alter compatibility even when the grouping key
    # itself stayed the same. Invalidate both the old and new keys, not just the
    # one that disappeared.
    for old, new in transitions:
        for label in (old, new):
            if ":" in label and not label.startswith(("none:", "other:")):
                affected_keys.add(label.split(":", 1)[1])
    for row in changed_rows:
        if row[1]:
            affected_keys.add(str(row[1]))
    evidence = audit_evidence(conn)
    affected_keys |= {
        str(key) for key in evidence["affected_keys"] if key
    }
    key_list = [key for key in affected_keys if key]
    live_snapshots = 0
    promoted_rows = 0
    if key_list:
        placeholders = ",".join("?" for _ in key_list)
        live_snapshots = conn.execute(
            f"SELECT COUNT(*) FROM valuation_snapshots WHERE invalidated_at IS NULL "
            f"AND product_key IN ({placeholders})",
            key_list,
        ).fetchone()[0]
        promoted_rows = conn.execute(
            f"SELECT COUNT(*) FROM deals d JOIN listings l ON l.id=d.listing_id "
            f"WHERE d.is_deal=1 AND l.status='active' AND l.product_key IN ({placeholders})",
            key_list,
        ).fetchone()[0]
    total_snapshots = conn.execute("SELECT COUNT(*) FROM valuation_snapshots").fetchone()[0]
    return {
        "listings": len(listings),
        "changed": changed,
        "confidence_only": confidence_only,
        "changed_rows": changed_rows,
        "transitions": transitions,
        "kind_changes": kind_changes,
        "confidence_before": confidence_before,
        "confidence_after": confidence_after,
        "identity_levels": identity_levels,
        "recovered_from_component": recovered_from_component,
        "new_valuable": new_valuable,
        "affected_keys": sorted(key_list),
        "live_snapshots": live_snapshots,
        "total_snapshots": total_snapshots,
        "promoted_on_affected_keys": promoted_rows,
        "evidence": evidence,
    }


def apply(conn: sqlite3.Connection, result: dict, *, revalue: bool) -> dict:
    conn.executemany(
        "UPDATE listings SET category=?, product_key=?, product_name=?, "
        "identification_confidence=?, is_faulty=?, is_wanted=?, is_bundle=?, "
        "listing_kind=?, flags=?, attrs=?, identity_level=?, identity_basis=? WHERE id=?",
        result["changed_rows"],
    )
    now = iso()
    evidence = result.get("evidence") or {}
    conn.executemany(
        "UPDATE market_observations SET product_key=?, kind=? WHERE id=?",
        evidence.get("observation_updates") or [],
    )
    conn.executemany(
        "UPDATE market_observations SET invalidated_at=?, invalid_reason=? WHERE id=? "
        "AND invalidated_at IS NULL",
        evidence.get("observation_invalidations") or [],
    )
    conn.executemany(
        "UPDATE market_comps SET product_key=?, kind=? WHERE id=?",
        evidence.get("comp_updates") or [],
    )
    invalidated = 0
    keys = result["affected_keys"]
    for start in range(0, len(keys), 400):
        chunk = keys[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        cursor = conn.execute(
            f"UPDATE valuation_snapshots SET invalidated_at=?, invalid_reason=? "
            f"WHERE product_key IN ({placeholders}) AND invalidated_at IS NULL",
            [now, "listing identity revised by identification fix", *chunk],
        )
        invalidated += cursor.rowcount or 0
    retired = conn.execute(
        """
        UPDATE market_observations
        SET invalidated_at = ?, invalid_reason = 'listing identity changed'
        WHERE invalidated_at IS NULL AND source_id LIKE 'listing:%'
          AND EXISTS (
              SELECT 1 FROM listings l
              WHERE ('listing:' || l.id) = market_observations.source_id
                AND (l.product_key IS NULL
                     OR l.product_key != market_observations.product_key
                     OR COALESCE(l.listing_kind, '') != COALESCE(market_observations.kind, ''))
          )
        """,
        (now,),
    ).rowcount or 0
    conn.commit()
    summary = {
        "invalidated_snapshots": invalidated,
        "retired_observations": retired,
        "observations_reassigned": evidence.get("observation_reassigned", 0),
        "observations_invalidated": evidence.get("observation_unresolved", 0),
        "market_comps_reassigned": evidence.get("comp_reassigned", 0),
        "market_comps_unresolved": evidence.get("comp_unresolved", 0),
    }
    if revalue:
        from app.db import Database
        from app.valuation import ValuationEngine

        db = Database(str(Path(settings.db_path)))
        try:
            summary["revalue"] = ValuationEngine(db).refresh(full=True)
        finally:
            db.close()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--revalue", action="store_true")
    parser.add_argument("--sample", type=int, default=0)
    args = parser.parse_args()
    path = Path(settings.db_path)
    if not path.exists():
        print(f"No database at {path}")
        return
    read_only = not args.apply
    conn = _connect(path, read_only)
    try:
        result = audit(conn, sample=args.sample or None)
        print(
            json.dumps(
                {
                    "database": str(path),
                    "listings": result["listings"],
                    "identity_changed": result["changed"],
                    "confidence_only_changes": result["confidence_only"],
                    "valuable_after": result["new_valuable"],
                    "recovered_from_component_identity": result["recovered_from_component"],
                    "confidence_before": dict(result["confidence_before"]),
                    "confidence_after": dict(result["confidence_after"]),
                    "identity_levels": dict(result["identity_levels"]),
                    "kind_changes": {
                        f"{old}->{new}": count
                        for (old, new), count in result["kind_changes"].most_common(12)
                    },
                    "top_transitions": [
                        {"from": old, "to": new, "count": count}
                        for (old, new), count in result["transitions"].most_common(15)
                    ],
                    "affected_product_keys": len(result["affected_keys"]),
                    "live_snapshots_on_affected_keys": result["live_snapshots"],
                    "total_snapshots": result["total_snapshots"],
                    "promoted_deals_on_affected_keys": result["promoted_on_affected_keys"],
                    "observations_reassigned": result["evidence"]["observation_reassigned"],
                    "observations_invalidated": result["evidence"]["observation_unresolved"],
                    "market_comps_reassigned": result["evidence"]["comp_reassigned"],
                    "market_comps_unresolved": result["evidence"]["comp_unresolved"],
                },
                indent=2,
            )
        )
        if args.apply:
            print(json.dumps(apply(conn, result, revalue=args.revalue), indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
