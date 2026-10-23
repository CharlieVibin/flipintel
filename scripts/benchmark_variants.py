"""Measure comparable-variant precision and recall on a hand-labelled holdout.

The labels in ``tests/fixtures/variant_holdout.json`` were adjudicated from the
titles themselves, not derived from ``app.variants``. Each pair asks the same
question the valuation engine asks before a price may enter a pool: is this
comparable the same sellable product, a different variant, or is a critical
attribute simply unknown?

    .venv/Scripts/python scripts/benchmark_variants.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.comparables import split_pool  # noqa: E402
from app.identification import identify_listing  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "variant_holdout.json"


def _comp(title: str) -> dict:
    return {
        "title": title,
        "price": 100.0,
        "marketplace": "ebay",
        "source": "ebay",
        "source_id": f"holdout:{abs(hash(title))}",
        "condition_norm": "good",
        "is_sold": 0,
    }


def evaluate() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for pair in data["pairs"]:
        ident = identify_listing(pair["candidate"])
        if not ident.product_key:
            raise AssertionError(f"holdout candidate was not identified: {pair['candidate']}")
        accepted, rejected = split_pool(
            [_comp(pair["comparable"])],
            kind=ident.kind,
            product_key=str(ident.product_key),
            candidate={
                "title": pair["candidate"],
                "product_key": ident.product_key,
                "product_name": ident.product_name,
                "listing_kind": ident.kind,
                "attrs": json.dumps(ident.attrs),
            },
        )
        predicted = (
            accepted[0]["match_quality"]
            if accepted
            else "incompatible"
        )
        rows.append({**pair, "predicted": predicted, "detail": rejected[0]["reason"] if rejected else ""})

    def ratio(num: int, den: int) -> float | None:
        return num / den if den else None

    incompatible = [row for row in rows if row["expected"] == "incompatible"]
    exact = [row for row in rows if row["expected"] == "exact"]
    uncertain = [row for row in rows if row["expected"] == "uncertain"]

    reject_tp = [row for row in incompatible if row["predicted"] == "incompatible"]
    reject_fp = [row for row in rows if row["expected"] != "incompatible" and row["predicted"] == "incompatible"]
    reject_fn = [row for row in incompatible if row["predicted"] != "incompatible"]
    exact_tp = [row for row in exact if row["predicted"] == "exact"]
    exact_fp = [row for row in rows if row["expected"] != "exact" and row["predicted"] == "exact"]
    exact_fn = [row for row in exact if row["predicted"] != "exact"]

    return {
        "pairs": len(rows),
        "expected_incompatible": len(incompatible),
        "expected_exact": len(exact),
        "expected_uncertain": len(uncertain),
        "rejection_precision": ratio(len(reject_tp), len(reject_tp) + len(reject_fp)),
        "rejection_recall": ratio(len(reject_tp), len(reject_tp) + len(reject_fn)),
        "exact_precision": ratio(len(exact_tp), len(exact_tp) + len(exact_fp)),
        "exact_recall": ratio(len(exact_tp), len(exact_tp) + len(exact_fn)),
        "false_matches": reject_fn + exact_fp,
        "missed_comparisons": exact_fn,
        "rows": rows,
    }


def main() -> None:
    result = evaluate()
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"rows", "false_matches", "missed_comparisons"}
            },
            indent=2,
        )
    )
    if result["false_matches"]:
        print("FALSE MATCHES")
        for row in result["false_matches"]:
            print(f"  {row['candidate']}  <>  {row['comparable']}: {row['predicted']} ({row['detail']})")
    if result["missed_comparisons"]:
        print("MISSED LEGITIMATE COMPARISONS")
        for row in result["missed_comparisons"]:
            print(f"  {row['candidate']}  <>  {row['comparable']}: {row['predicted']} ({row['detail']})")


if __name__ == "__main__":
    main()
