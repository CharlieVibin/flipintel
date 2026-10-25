"""A manually labelled Phase 6 holdout for physical-product role and identity."""
from __future__ import annotations

import json
from pathlib import Path

from app.identification import identify_listing

FIXTURE = Path(__file__).parent / "fixtures" / "phase6_holdout.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _score() -> dict:
    role_correct = 0
    kind_correct = 0
    kind_total = 0
    false_positive: list[str] = []
    false_negative: list[str] = []
    failures: list[str] = []
    for case in CASES:
        result = identify_listing(case["title"])
        if result.sale_role == case["role"]:
            role_correct += 1
        else:
            failures.append(
                f"{case['title'][:60]}: role {result.sale_role} != {case['role']}"
            )
        if case["key"] and not result.product_key:
            false_negative.append(case["title"])
        elif not case["key"] and result.product_key:
            false_positive.append(f"{case['title']} -> {result.product_key}")
        expected_kind = case.get("kind")
        if expected_kind:
            kind_total += 1
            if result.kind == expected_kind:
                kind_correct += 1
            else:
                failures.append(
                    f"{case['title'][:60]}: kind {result.kind} != {expected_kind}"
                )
    return {
        "cases": len(CASES),
        "role_accuracy": role_correct / len(CASES),
        "kind_accuracy": kind_correct / max(1, kind_total),
        "false_positives": false_positive,
        "false_negatives": false_negative,
        "failures": failures,
    }


def test_labelled_holdout_role_and_identity_quality():
    result = _score()
    assert result["role_accuracy"] >= 0.93, result["failures"]
    assert result["kind_accuracy"] >= 0.95, result["failures"]
    # A non-product must never receive a comparable product key.
    assert not result["false_positives"], result["false_positives"]
    # Missing one unusual complete product is tolerated; inventing identities is not.
    assert len(result["false_negatives"]) <= 1, result["false_negatives"]


def test_holdout_has_no_unchecked_box_or_accessory_as_product():
    for title in (
        "Empty PS5 box",
        "Otter box iPhone 17 PRO",
        "RTX 5080/5090 artisan keycap",
        "Nintendo Switch 1TB microSD card",
        "Steam Deck 1TB OLED case",
    ):
        result = identify_listing(title)
        assert result.product_key is None, (title, result.product_key)
        assert result.sale_role != "device", (title, result.sale_role)
