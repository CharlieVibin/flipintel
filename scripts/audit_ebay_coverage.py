"""Measure how much live eBay UK inventory FlipIntel actually reaches.

This is a diagnostic, not a collector. It asks eBay for the newest results in
the branches enabled by the saved preset, then checks whether each result is
already stored. Missing rows are classified as outside the preset, over the
saved purchase budget, or genuinely missed inside an enabled branch.

Run it with the dashboard stopped or running; it opens its own database
connection and eBay client. It does spend Browse calls, so keep the query list
small.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.categories import category_by_id  # noqa: E402
from app.collectors.ebay import EbayCollector  # noqa: E402
from app.db import Database  # noqa: E402
from app.identification import identify_listing  # noqa: E402
from app.scan_config import load_scan_config  # noqa: E402
from app.scan_taxonomy import mapped_category_ids  # noqa: E402

QUERIES = (
    "RTX 3060",
    "DDR4 16GB",
    "Ryzen 5 5600",
    "B550 motherboard",
    "1TB NVMe SSD",
    "Elgato capture card",
    "WiFi 6 PCIe card",
    "Lian Li case",
)
CATEGORY_IDS = ("27386", "170083", "164", "1244", "182085")


def _stored(db: Database, external_id: str) -> dict[str, Any] | None:
    return db.query_one(
        """
        SELECT l.id, l.product_key, l.category, l.condition_norm,
               d.eligibility, d.eligibility_reason
        FROM listings l LEFT JOIN deals d ON d.listing_id = l.id
        WHERE l.marketplace='ebay' AND l.external_id=?
        """,
        (external_id,),
    )


def _classify(
    db: Database,
    collector: EbayCollector,
    item,
    allowed_ids: set[str],
    limit_low: float,
    limit_high: float,
) -> dict[str, Any]:
    total = float(item.price or 0) + float(item.shipping or 0)
    stored = _stored(db, item.external_id)
    row: dict[str, Any] = {
        "external_id": item.external_id,
        "title": item.title,
        "price": item.price,
        "shipping": item.shipping,
        "total": round(total, 2),
        "category_id": item.category_id,
        "stored": stored is not None,
    }
    if stored:
        row["stored_product_key"] = stored["product_key"]
        row["eligibility"] = stored["eligibility"]
        row["eligibility_reason"] = stored["eligibility_reason"]
        return row
    row["category_enabled"] = str(item.category_id) in allowed_ids
    row["within_budget"] = limit_low <= total <= limit_high
    ident = identify_listing(item.title, item.description)
    row["identified_key"] = ident.product_key
    row["identified_kind"] = ident.kind
    if not row["category_enabled"]:
        row["reason"] = "category not enabled by the active preset"
    elif not row["within_budget"]:
        row["reason"] = "total purchase cost is outside the saved budget"
    elif ident.product_key is None:
        row["reason"] = "inside the preset, but no exact product identity"
    else:
        row["reason"] = "missed inside an enabled category (pagination/sweep)"
    return row


async def main() -> None:
    db = Database()
    config = load_scan_config(db)
    low, high = config.server_price_bounds("ebay")
    allowed_ids = set(mapped_category_ids("ebay", config.categories))
    collector = EbayCollector(db)
    collector.scan_price_min, collector.scan_price_max = low, high
    report: dict[str, Any] = {
        "preset": config.active_preset,
        "budget": [low, high],
        "enabled_leaves": sorted(config.categories),
        "enabled_ebay_branches": len(allowed_ids),
        "category_ids": sorted(allowed_ids),
        "queries": [],
        "category_pages": [],
    }
    try:
        seen: set[str] = set()
        for query in QUERIES:
            outcome = await collector.search([query], max_pages=1)
            rows = []
            for item in outcome.items:
                if item.external_id in seen:
                    continue
                seen.add(item.external_id)
                rows.append(
                    _classify(db, collector, item, allowed_ids, low, high)
                )
            report["queries"].append(
                {
                    "query": query,
                    "status": outcome.status,
                    "results": len(outcome.items),
                    "rows": rows,
                }
            )
        for category_id in CATEGORY_IDS:
            category = category_by_id("ebay", category_id)
            if not category or category_id not in allowed_ids:
                continue
            page = await collector.category_page(category, 1)
            rows = [
                _classify(db, collector, item, allowed_ids, low, high)
                for item in page.items
            ]
            report["category_pages"].append(
                {
                    "category_id": category_id,
                    "label": category.label,
                    "results": len(page.items),
                    "provider_total": page.total,
                    "rows": rows,
                }
            )
    finally:
        await collector.close()
        db.close()

    rows = [
        row
        for block in report["queries"] + report["category_pages"]
        for row in block["rows"]
    ]
    unique = {row["external_id"]: row for row in rows}
    report["summary"] = {
        "sampled": len(unique),
        "stored": sum(1 for row in unique.values() if row["stored"]),
        "missing": sum(1 for row in unique.values() if not row["stored"]),
        "missing_category_disabled": sum(
            1
            for row in unique.values()
            if not row["stored"] and row.get("category_enabled") is False
        ),
        "missing_over_budget": sum(
            1
            for row in unique.values()
            if not row["stored"] and row.get("within_budget") is False
        ),
        "missing_no_identity": sum(
            1
            for row in unique.values()
            if not row["stored"]
            and row.get("category_enabled") is True
            and row.get("within_budget") is True
            and not row.get("identified_key")
        ),
        "missing_addressable": sum(
            1
            for row in unique.values()
            if not row["stored"]
            and row.get("category_enabled") is True
            and row.get("within_budget") is True
            and row.get("identified_key")
        ),
    }
    print(json.dumps(report["summary"], indent=2))
    for block in report["queries"]:
        missing = [row for row in block["rows"] if not row["stored"]]
        print(
            f"\nquery {block['query']!r}: {block['results']} results, "
            f"{len(missing)} not stored"
        )
        for row in missing[:5]:
            print(
                f"  £{row['total']:.2f} cat={row['category_id']} "
                f"enabled={row.get('category_enabled')} "
                f"key={row.get('identified_key')} | {row['title'][:80]} "
                f"| {row.get('reason')}"
            )
    output = ROOT / "data" / "ebay_coverage_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {output}")


if __name__ == "__main__":
    asyncio.run(main())
