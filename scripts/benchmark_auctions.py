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
) -> tuple[str | None, str | None, float | None, float | None, str | None, int, str | None]:
    """Identify a result and derive a ceiling from *fresh* stored price memory.

    The snapshot must be inside the seven-day reuse window. An old price is
    history, not a current liquidation value, so it cannot justify a bid.
    """
    ident = identify_listing(item.title, item.description or "")
    if not ident.product_key:
        return None, None, None, None, None, 0, None
    snapshot = db.query_one(
        "SELECT resale_good, evidence_level, sample_size, refreshed_at "
        "FROM valuation_snapshots "
        "WHERE product_key=? AND invalidated_at IS NULL "
        "AND julianday(refreshed_at) >= julianday('now', '-7 days') "
        "ORDER BY id DESC LIMIT 1",
        (ident.product_key,),
    )
    resale = float(snapshot["resale_good"]) if snapshot and snapshot["resale_good"] else None
    if not resale:
        return ident.product_key, ident.identity_level, None, None, None, 0, None
    config = ValuationConfig.from_db(db)
    fees = resale * config.ebay_fvf + config.ebay_fixed_fee
    # Same cost model as ValuationEngine.price_ceiling(). Leaving out the
    # collection and other expense lines would overstate the ceiling here and
    # make the benchmark disagree with the deal the dashboard shows.
    proceeds = (
        resale - fees - config.resale_shipping - config.packaging_cost
        - config.collection_cost - config.other_costs
    )
    by_profit = proceeds - max(config.min_profit, resale * 0.08)
    by_roi = proceeds / (1.0 + config.min_roi)
    return (
        ident.product_key,
        ident.identity_level,
        round(max(0.0, min(by_profit, by_roi)), 2),
        resale,
        snapshot["evidence_level"] if snapshot else None,
        int(snapshot["sample_size"] or 0),
        snapshot["refreshed_at"],
    )


def _summarise(label: str, outcome, db: Database) -> dict:
    keys: Counter = Counter()
    levels: Counter = Counter()
    under_ceiling = 0
    under_ceiling_usable = 0
    with_ceiling = 0
    ending_soon = 0
    samples: list[dict] = []
    now = datetime.now(timezone.utc)
    for item in outcome.items:
        key, level, ceiling, resale, evidence, sample, _refreshed = _profitable_ceiling(db, item)
        if key:
            keys[key] += 1
            levels[level or "unknown"] += 1
        if ceiling is not None:
            with_ceiling += 1
            usable = sample >= 5 and evidence in {"limited", "moderate", "strong"}
            bid = item.current_bid if item.current_bid is not None else item.price
            if bid is not None and bid <= ceiling:
                under_ceiling += 1
                if usable:
                    under_ceiling_usable += 1
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
                    config = ValuationConfig.from_db(db)
                    total_cost = bid + float(item.shipping or 0)
                    fees = resale * config.ebay_fvf + config.ebay_fixed_fee
                    profit = (
                        resale - fees - config.resale_shipping - config.packaging_cost
                        - config.collection_cost - config.other_costs - total_cost
                    )
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
                            "sample": sample,
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
        "under_ceiling_with_usable_evidence": under_ceiling_usable,
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


def _usable(evidence: str | None, sample: int) -> bool:
    return sample >= 5 and evidence in {"limited", "moderate", "strong"}


async def _recheck(summary: dict, outcome, collector: EbayCollector, db: Database) -> None:
    """Re-fetch the actionable shortlist and report it at the current bid.

    This mirrors what the scanner does before alerting: discovery prices are
    minutes old and an auction can pass its ceiling in that time. It costs one
    Browse request (up to 20 IDs), reported separately from discovery.
    """
    now = datetime.now(timezone.utc)
    shortlist = []
    for item in outcome.items:
        _key, _level, ceiling, _resale, evidence, sample, _refreshed = _profitable_ceiling(db, item)
        bid = item.current_bid if item.current_bid is not None else item.price
        if ceiling is None or bid is None or bid > ceiling:
            continue
        if not _usable(evidence, sample):
            continue
        shortlist.append(item)
    if not shortlist:
        summary["recheck"] = {"items": 0}
        return
    try:
        fresh, _status = await collector.get_items(
            [str(item.external_id) for item in shortlist if item.external_id][:20]
        )
    except Exception as exc:  # noqa: BLE001 - benchmark reports, never raises
        summary["recheck"] = {"items": len(shortlist), "error": type(exc).__name__}
        return
    by_id = {str(item.external_id): item for item in fresh}
    ended = still = soon = 0
    rows = []
    for original in shortlist:
        item = by_id.get(str(original.external_id))
        if item is None:
            continue
        minutes = _minutes_left(item, now)
        if minutes is not None and minutes <= 0:
            ended += 1
            continue
        _key, _level, ceiling, resale, evidence, sample, _refreshed = _profitable_ceiling(db, item)
        bid = item.current_bid if item.current_bid is not None else item.price
        if ceiling is None or bid is None or bid > ceiling or not _usable(evidence, sample):
            continue
        still += 1
        if minutes is not None and minutes <= 10:
            soon += 1
            rows.append(
                {
                    "title": item.title[:90],
                    "bid": bid,
                    "ceiling": ceiling,
                    "resale": resale,
                    "minutes_left": minutes,
                    "evidence": evidence,
                    "sample": sample,
                }
            )
    summary["recheck"] = {
        "items": len(shortlist),
        "returned": len(fresh),
        "ended": ended,
        "still_under_ceiling": still,
        "attractive_within_10min": soon,
        "samples": rows[:5],
    }


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
        keyword_summary = _summarise("keyword", keyword, db)
        await _recheck(keyword_summary, keyword, collector, db)
        print(keyword_summary)
        catalogue = [c for c in categories_for("ebay") if c.priority >= 2]
        category = await collector.search_auction_categories(
            [(c.category_id, c.label) for c in catalogue],
            ending_within_minutes=window,
            max_requests=budget,
        )
        category_summary = _summarise("category", category, db)
        await _recheck(category_summary, category, collector, db)
        print(category_summary)
        # How much of the whole catalogue one pass with this budget covers.
        recheck_requests = sum(
            1 for summary in (keyword_summary, category_summary)
            if (summary.get("recheck") or {}).get("items")
        )
        print(
            {
                "catalogue_categories": len(catalogue),
                "budget": budget,
                "passes_to_cover_catalogue": round(len(catalogue) / max(1, budget), 2),
                "discovery_requests": keyword_summary["requests"] + category_summary["requests"],
                "recheck_requests": recheck_requests,
                "total_api_requests": (
                    keyword_summary["requests"] + category_summary["requests"] + recheck_requests
                ),
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
