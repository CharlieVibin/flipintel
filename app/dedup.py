"""Conservative listing duplicate detection.

Marketplaces list the same physical item more than once: a seller republishes
an identical title at the same price, or cross-posts to another marketplace.
Those rows are not additional opportunities and must not occupy review cards.
Rows are never merged or deleted; a later listing points at the earliest one via
``duplicate_of`` and eligibility excludes it.

Two deliberately different rules are used:

1. Same marketplace, same seller, same title and same rounded price. This is a
   relist of one product, including mass-listed commodity storage.
2. Different marketplaces, same product key, near-identical title and a price
   within 2.5% (or £3). Two sellers offering the same model are *not* merged
   just because they share a product key.
"""
from __future__ import annotations

from .db import Database
from .utils import hours_ago, jaccard, token_set


def mark_duplicates(db: Database) -> int:
    """Flag probable duplicates. Returns how many rows were newly linked."""
    marked = 0
    keep_select = (
        "SELECT marketplace, seller, title, ROUND(price, 2) AS price_key, "
        "MIN(id) AS keep_id, COUNT(*) AS n FROM listings "
        "WHERE status='active' AND seller IS NOT NULL AND seller != '' "
        "AND last_seen >= ? GROUP BY marketplace, seller, title, ROUND(price, 2) "
        "HAVING n > 1"
    )
    repeated = db.query(
        f"""
        SELECT l.id, g.keep_id
        FROM listings l JOIN ({keep_select}) g
          ON g.marketplace = l.marketplace AND g.seller = l.seller
         AND g.title = l.title AND g.price_key = ROUND(l.price, 2)
        WHERE l.status = 'active' AND l.id != g.keep_id
          AND (l.duplicate_of IS NULL OR l.duplicate_of != g.keep_id)
        """,
        (hours_ago(24 * 30),),
    )
    for row in repeated:
        db.execute(
            "UPDATE listings SET duplicate_of=?, duplicate_score=1.0 WHERE id=?",
            (int(row["keep_id"]), int(row["id"])),
        )
        marked += 1

    rows = db.query(
        """
        SELECT id, marketplace, title, price, product_key, duplicate_of
        FROM listings
        WHERE status='active' AND product_key IS NOT NULL
          AND last_seen >= ? AND is_faulty=0 AND is_wanted=0 AND is_bundle=0
        ORDER BY id DESC LIMIT 700
        """,
        (hours_ago(24 * 14),),
    )
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(str(row["product_key"]), []).append(row)
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda item: item["id"])
        for index, row in enumerate(group):
            if row.get("duplicate_of"):
                continue
            row_tokens = token_set(row["title"])
            for earlier in group[:index]:
                if earlier["marketplace"] == row["marketplace"]:
                    # Same-marketplace repeats are handled by the seller pass
                    # above; a shared product key across sellers is a model,
                    # not the same physical item.
                    continue
                price_a = float(row["price"] or 0)
                price_b = float(earlier["price"] or 0)
                if min(price_a, price_b) <= 0:
                    continue
                tolerance = max(3.0, max(price_a, price_b) * 0.025)
                if abs(price_a - price_b) > tolerance:
                    continue
                similarity = jaccard(row_tokens, token_set(earlier["title"]))
                if similarity < 0.72:
                    continue
                score = round(
                    similarity
                    * (1.0 - min(1.0, abs(price_a - price_b) / max(price_a, price_b))),
                    3,
                )
                db.execute(
                    "UPDATE listings SET duplicate_of=?, duplicate_score=? WHERE id=?",
                    (earlier["id"], score, row["id"]),
                )
                marked += 1
                break
    return marked
