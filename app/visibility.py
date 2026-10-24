"""Saved-preset visibility for listings that are already in the database.

The scanner has always applied the active category, marketplace and budget
rules to *new* marketplace requests. Stored rows were never checked again, so a
listing collected under an earlier preset kept appearing in the dashboard after
its category was disabled or its price rose above the saved budget.

This module owns the one definition of "inside the active preset". It is used by
the API queries (so pagination, counts and statistics agree), by the Discord
notifier and by the scanner's own stored-row checks. Nothing here deletes or
rewrites market history: a row outside the preset stays in the database and is
only labelled as outside the current configuration.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Iterable

from .scan_config import ScanConfiguration, load_scan_config, scan_leaf_for
from .scan_taxonomy import NODE_BY_KEY

SCAN_LEAF_SQL = "ALTER TABLE listings ADD COLUMN scan_leaf TEXT"


def _ident(row: dict[str, Any]) -> SimpleNamespace:
    try:
        flags = json.loads(row.get("flags") or "[]")
    except (TypeError, ValueError):
        flags = []
    try:
        attrs = json.loads(row.get("attrs") or "{}")
    except (TypeError, ValueError):
        attrs = {}
    return SimpleNamespace(
        category=row.get("category") or "other",
        product_key=row.get("product_key") or "",
        flags=flags,
        attrs=attrs,
    )


def leaf_for_row(row: dict[str, Any]) -> str:
    """Classify a stored row with the current taxonomy rules."""
    return scan_leaf_for(
        _ident(row),
        row.get("title") or row.get("product_name") or "",
        None,
        str(row.get("marketplace") or ""),
    )


def leaf_label(leaf: str | None) -> str:
    node = NODE_BY_KEY.get(str(leaf or ""))
    return node.label if node else (leaf or "Unknown category")


def backfill_scan_leafs(db, *, force: bool = False, batch: int = 2000) -> int:
    """Store the taxonomy leaf for rows that do not have one yet.

    Identity and taxonomy fixes change the leaf, so ``force`` recomputes every
    row. This deliberately updates a derived column only; listings, prices and
    observations are untouched.
    """
    where = "" if force else "WHERE scan_leaf IS NULL OR scan_leaf = ''"
    rows = db.query(
        "SELECT id, title, product_name, marketplace, category, product_key, flags, attrs "
        f"FROM listings {where}"
    )
    if not rows:
        return 0
    updates = [(leaf_for_row(row), int(row["id"])) for row in rows]
    for start in range(0, len(updates), batch):
        db.executemany(
            "UPDATE listings SET scan_leaf=? WHERE id=?", updates[start : start + batch]
        )
    return len(updates)


def effective_price_sql(alias: str = "l") -> str:
    """The purchase price that belongs to the listing's sale type.

    An auction's ``price`` is already mirrored from its current bid, but the
    explicit expression keeps the two concepts separate in every report.
    """
    return (
        f"(CASE WHEN {alias}.is_auction = 1 "
        f"THEN COALESCE({alias}.current_bid, {alias}.price) ELSE {alias}.price END)"
    )


def total_cost_sql(
    config: ScanConfiguration, alias: str = "l"
) -> tuple[str, list[Any]]:
    """Total purchase cost, matching ``ScanConfiguration.item_total``.

    Vinted cards do not expose the buyer-protection fee or the chosen delivery
    service, so the saved estimates are included rather than pretending the
    item price is the total. eBay and Gumtree add the listed postage.
    """
    price = effective_price_sql(alias)
    sql = (
        f"(CASE WHEN {alias}.marketplace = 'vinted' "
        f"THEN {alias}.price + ? + ROUND({alias}.price * ? + ?, 2) "
        f"ELSE {price} + COALESCE({alias}.shipping, 0) END)"
    )
    return sql, [
        float(config.vinted_shipping),
        float(config.vinted_buyer_fee_pct),
        float(config.vinted_buyer_fee_fixed),
    ]


def preset_filter(
    db, config: ScanConfiguration | None = None, alias: str = "l"
) -> tuple[list[str], list[Any], ScanConfiguration]:
    """SQL clauses selecting only rows inside the saved preset.

    A missing or unsaved configuration allows nothing: the scanner itself is
    paused in that state, so the dashboard must not imply an active scan.
    """
    cfg = config or load_scan_config(db)
    clauses: list[str] = []
    params: list[Any] = []
    if not cfg.confirmed:
        # No preset has been saved yet. The scanner is paused, but existing
        # history stays visible; there is no active rule set to enforce.
        return clauses, params, cfg
    leaves = sorted(cfg.categories)
    if leaves:
        clauses.append(f"{alias}.scan_leaf IN ({','.join('?' for _ in leaves)})")
        params.extend(leaves)
    else:
        clauses.append("0")
    markets = sorted(name for name, on in cfg.marketplaces.items() if on)
    if markets:
        clauses.append(f"{alias}.marketplace IN ({','.join('?' for _ in markets)})")
        params.extend(markets)
    else:
        clauses.append("0")
    total_sql, total_params = total_cost_sql(cfg, alias)
    if cfg.min_price > 0:
        clauses.append(f"{total_sql} >= ?")
        params.extend(total_params + [cfg.min_price])
    if cfg.max_price > 0:
        clauses.append(f"{total_sql} <= ?")
        params.extend(total_params + [cfg.max_price])
    return clauses, params, cfg


def row_total(config: ScanConfiguration, row: dict[str, Any]) -> tuple[float, str]:
    price = (
        row.get("current_bid")
        if row.get("is_auction") and row.get("current_bid") is not None
        else row.get("price")
    )
    return config.item_total(
        str(row.get("marketplace") or ""),
        float(price or 0),
        float(row.get("shipping") or 0),
    )


def row_in_preset(
    config: ScanConfiguration, row: dict[str, Any]
) -> tuple[bool, list[str]]:
    """Whether a stored row is displayable under the active preset, with reasons."""
    if not config.confirmed:
        return True, []
    reasons: list[str] = []
    leaf = str(row.get("scan_leaf") or leaf_for_row(row))
    if not config.allows(leaf):
        reasons.append(f"{leaf_label(leaf)} is disabled in the saved preset")
    marketplace = str(row.get("marketplace") or "")
    if not config.marketplace_enabled(marketplace):
        reasons.append(f"{marketplace.title()} is disabled in the saved preset")
    total, _basis = row_total(config, row)
    if config.min_price > 0 and total < config.min_price:
        reasons.append(
            f"Total cost £{total:,.2f} is below the £{config.min_price:,.2f} minimum"
        )
    if config.max_price > 0 and total > config.max_price:
        reasons.append(
            f"Total cost £{total:,.2f} is above the £{config.max_price:,.2f} maximum"
        )
    return not reasons, reasons


def preset_summary(
    config: ScanConfiguration, *, hidden: int = 0, unfiltered: int = 0
) -> dict[str, Any]:
    return {
        "active_preset": config.active_preset,
        "confirmed": config.confirmed,
        "min_price": config.min_price,
        "max_price": config.max_price,
        "marketplaces": dict(config.marketplaces),
        "enabled_categories": sorted(config.categories),
        "enabled_count": config.enabled_count,
        "hidden": int(hidden),
        "unfiltered": int(unfiltered),
    }


def annotate_preset(
    config: ScanConfiguration, rows: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Label rows that are outside the preset when history mode is requested."""
    for row in rows:
        inside, reasons = row_in_preset(config, row)
        row["outside_preset"] = not inside
        row["preset_reasons"] = reasons
    return list(rows)
