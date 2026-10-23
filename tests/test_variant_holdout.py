"""Independent variant-compatibility holdout.

The fixture's 69 pairs were labelled from the titles by hand before the
comparator was run. This test is deliberately separate from the targeted
regressions in ``test_variant_matching.py`` so it can expose overfitting.
"""
from __future__ import annotations

from scripts.benchmark_variants import evaluate


def test_holdout_rejection_precision_and_recall():
    result = evaluate()
    assert result["pairs"] >= 60
    assert result["expected_incompatible"] >= 40
    assert result["expected_exact"] >= 10
    assert result["rejection_precision"] is not None
    assert result["rejection_precision"] >= 0.95, result["false_matches"]
    assert result["rejection_recall"] is not None
    assert result["rejection_recall"] >= 0.95, result["missed_comparisons"]
    assert result["exact_precision"] >= 0.95, result["false_matches"]
    assert result["exact_recall"] >= 0.95, result["missed_comparisons"]


def test_holdout_never_merges_critical_variant_families():
    result = evaluate()
    merged = [
        row
        for row in result["rows"]
        if row["expected"] == "incompatible" and row["predicted"] in {"exact", "uncertain"}
    ]
    assert not merged, [(row["candidate"], row["comparable"], row["predicted"]) for row in merged[:5]]
