"""The 240-listing stratified identity benchmark.

Labels are independent of the classifier: a coarse rule oracle plus manual
adjudication of every disagreement. Unverified rows are excluded from the
accuracy denominators; the benchmark therefore cannot pass by agreeing with
itself on ambiguous titles.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.identification import identify_listing

FIXTURE = Path(__file__).parent / "fixtures" / "listing_benchmark_240.json"
OVERRIDES = Path(__file__).parent / "fixtures" / "listing_benchmark_overrides.json"


def _load() -> list[dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))["overrides"]
    listings = data["listings"]
    for row in listings:
        override = overrides.get(str(row["id"]))
        if override:
            row["expected_valued"] = bool(override["expected_valued"])
            row["label_status"] = "verified"
    return listings


def test_benchmark_is_large_and_stratified():
    listings = _load()
    assert len(listings) >= 200
    assert len({row["marketplace"] for row in listings}) >= 3
    assert len({row["predicted_kind"] for row in listings}) >= 8
    assert sum(1 for row in listings if row["label_status"] != "verified") >= 40


def test_identity_precision_recall_and_kind_accuracy():
    rows = _load()
    results = []
    for row in rows:
        ident = identify_listing(row["title"])
        results.append(
            {
                **row,
                "kind": ident.kind,
                "valued": bool(ident.product_key) and not ident.is_bundle,
            }
        )
    verified = [row for row in results if row["label_status"] == "verified"]

    kind_rows = [
        row for row in verified
        if row["expected_valued"] is True and row["expected_kind"] != "unknown"
    ]
    kind_correct = [row for row in kind_rows if row["kind"] == row["expected_kind"]]

    value_rows = [row for row in verified if row["expected_valued"] is not None]
    true_positive = [
        row for row in value_rows
        if row["expected_valued"] and row["valued"]
    ]
    false_positive = [
        row for row in value_rows
        if not row["expected_valued"] and row["valued"]
    ]
    false_negative = [
        row for row in value_rows
        if row["expected_valued"] and not row["valued"]
    ]

    precision = len(true_positive) / max(1, len(true_positive) + len(false_positive))
    recall = len(true_positive) / max(1, len(true_positive) + len(false_negative))
    kind_accuracy = len(kind_correct) / max(1, len(kind_rows))

    assert precision >= 0.98, (precision, [row["title"] for row in false_positive][:5])
    assert recall >= 0.94, (recall, [row["title"] for row in false_negative][:5])
    assert kind_accuracy >= 0.97, (
        kind_accuracy,
        [(row["expected_kind"], row["kind"]) for row in kind_rows if row["kind"] != row["expected_kind"]][:5],
    )
