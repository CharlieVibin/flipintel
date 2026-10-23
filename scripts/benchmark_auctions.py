"""Benchmark eBay ending-soon auction discovery strategies.

Runs the old keyword strategy and the category strategy against the live Browse
API with the *same* request budget, then reports what each returned inside the
ending-time window, how many were identified, and how many already had a
profitable bid ceiling according to stored price memory. Read-only: it records
API usage, but never writes listings or deals.

    .venv/Scripts/python scripts/benchmark_auctions.py --budget 2
    .venv/Scripts/python scripts/benchmark_auctions.py --budget 4 --window 30
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.categories import categories_for  # noqa: E402
from app.collectors.ebay import EbayCollector  # noqa: E402
from app.db import Database  # noqa: E402
from app.identification import identify_listing  # noqa: E402
from app.valuation import ValuationConfig  # noqa: E402

AUCTION_KEYWORD_QUERIES = ["graphics card -games", "playstation 5 console"]


def _profitable_ceiling(
    db: Database, item
) -> tuple[str | None, str | None, float | None, float | None, str | None]:
    """Identify a result and derive a ceiling from stored price memory only."""
    ident = identify_listing(item.title, item.description or "")
    if not ident.product_key:
        return None, None, None, None, None
    snapshot = db.query_one(
        "SELECT resale_good, evidence_level, refreshed_at FROM valuation_snapshots "
        "WHERE product_key=? AND invalidated_at IS NULL ORDER BY id DESC LIMIT 1",
        (ident.product_key,),
    )
    resale = float(snapshot["resale_good"]) if snapshot and snapshot["resale_good"] else None
    if not resale:
        return ident.product_key, ident.identity_level, None, None, None
    config = ValuationConfig.from_db(db)
    fees = resale * config.ebay_fvf + config.ebay_fixed_fee
    proceeds = resale - fees - config.resale_shipping - config.packaging_cost
    by_profit = proceeds - max(config.min_profit, resale * 0.08)
    by_roi = proceeds / (1.0 + config.min_roi)
    return (
        ident.product_key,
        ident.identity_level,
        round(max(0.0, min(by_profit, by_roi)), 2),
        resale,
        snapshot["evidence_level"] if snapshot else None,
    )


def _summarise(label: str, outcome, db: Database) -> dict:
    keys: Counter = Counter()
    levels: Counter = Counter()
    under_ceiling = 0
    with_ceiling = 0
    ending_soon = 0
    samples: list[dict] = []
    now = datetime.now(timezone.utc)
    for item in outcome.items:
        key, level, ceiling, resale, evidence = _profitable_ceiling(db, item)
        if key:
            keys[key] += 1
            levels[level or "unknown"] += 1
        if ceiling is not None:
            with_ceiling += 1
            bid = item.current_bid if item.current_bid is not None else item.price
            if bid is not None and bid <= ceiling:
                under_ceiling += 1
                try:
                    end = datetime.fromisoformat(
                        str(item.auction_end_at).replace("Z", "+00:00")
                    )
                except (TypeError, ValueError):
                    end = None
                minutes_left = (
                    int((end - now).total_seconds() / 60) if end else None
                )
                if minutes_left is not None and minutes_left <= 10:
                    ending_soon += 1
                if len(samples) < 5 and resale:
                    total_cost = bid + float(item.shipping or 0)
                    fees = resale * 0.1322 + 0.30
                    profit = resale - fees - 4.50 - 1.00 - total_cost
                    samples.append(
                        {
                            "title": item.title[:90],
                            "bid": bid,
                            "total_cost": round(total_cost, 2),
                            "ceiling": ceiling,
                            "resale": resale,
                            "profit_at_bid": round(profit, 2),
                            "roi_at_bid": round(profit / total_cost, 3) if total_cost else None,
                            "minutes_left": minutes_left,
                            "key": key,
                            "identity": level,
                            "evidence": evidence,
                        }
                    )
    detail = outcome.detail or {}
    duplicate_listings = sum(count - 1 for count in keys.values() if count > 1)
    return {
        "strategy": label,
        "status": outcome.status,
        "requests": int(detail.get("requests") or outcome.pages or 0),
        "scanned": int(detail.get("scanned") or 0),
        "in_window": len(outcome.items),
        "in_window_last_10min": sum(
            1 for item in outcome.items
            if _minutes_left(item, now) is not None and _minutes_left(item, now) <= 10
        ),
        "unique_products": len(keys),
        "duplicate_listings": duplicate_listings,
        "identity_moderate_or_better": sum(
            levels[name] for name in ("moderate", "strong")
        ),
        "with_stored_valuation": with_ceiling,
        "under_profitable_ceiling": under_ceiling,
        "under_ceiling_ending_within_10min": ending_soon,
        "samples": sorted(samples, key=lambda row: row["roi_at_bid"] or 0, reverse=True),
        "error": outcome.error,
    }


def _minutes_left(item, now: datetime) -> int | None:
    try:
        end = datetime.fromisoformat(str(item.auction_end_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return int((end - now).total_seconds() / 60)


async def run(budget: int, window: int) -> None:
    db = Database()
    collector = EbayCollector(db)
    try:
        available, reason = collector.available
        if not available:
            print(f"eBay unavailable: {reason}")
            return
        keyword = await collector.search_auctions(
            AUCTION_KEYWORD_QUERIES, ending_within_minutes=window, max_queries=budget
        )
        print(_summarise("keyword", keyword, db))
        catalogue = [c for c in categories_for("ebay") if c.priority >= 2]
        category = await collector.search_auction_categories(
            [(c.category_id, c.label) for c in catalogue],
            ending_within_minutes=window,
            max_requests=budget,
        )
        print(_summarise("category", category, db))
        # How much of the whole catalogue one pass with this budget covers.
        print(
            {
                "catalogue_categories": len(catalogue),
                "budget": budget,
                "passes_to_cover_catalogue": round(len(catalogue) / max(1, budget), 2),
            }
        )
    finally:
        await collector.close()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=int, default=2)
    parser.add_argument("--window", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(run(args.budget, args.window))
