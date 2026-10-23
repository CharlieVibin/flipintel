"""Benchmark product identity on a stratified sample of real stored listings.

The 27-case regression fixture is a development set. This benchmark samples the
live database (marketplace × classifier kind × price band) with a fixed seed so
every run measures the same listings. Ground truth comes from an *independent*
coarse rule set, not from the production classifier:

* ``expected_valued`` is ``False`` only when the title unambiguously describes an
  accessory, game, empty box, parts-only item or wanted advert, and ``True`` only
  when it names a specific device model with no accessory/game wording.
* ``expected_kind`` is set only when the title's device nouns are unambiguous.
* Everything else is ``unknown`` and excluded from accuracy denominators.

That keeps ambiguous titles labelled unverified instead of using the model's own
prediction as proof. Disagreements are printed for manual review.

    .venv/Scripts/python scripts/benchmark_listings.py
    .venv/Scripts/python scripts/benchmark_listings.py --sample 240 --write
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.identification import identify_listing  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "listing_benchmark_240.json"
OVERRIDES = ROOT / "tests" / "fixtures" / "listing_benchmark_overrides.json"

ACCESSORY_RE = re.compile(
    r"\b(case|cases|cover|covers|faceplate|faceplates|skin|skins|sleeve|bag|strap|"
    r"screen protector|controller|controllers|gamepad|headset|headphones|charger|"
    r"charging dock|dock|stand|bracket|holder|cable|cables|adapter|stylus|pen|"
    r"empty box|box only|packaging|spares?|repairs?|faulty|not working|untested|"
    r"parts?|motherboard only|shell|frame only|wanted|looking for|wtb|"
    r"game|games|cartridge|disc only|code|key|dlc|manual|steelbook)\b",
    re.I,
)
MODEL_RES = {
    "gpu": re.compile(r"\b(?:rtx|gtx|rx)\s*\d{3,4}\b", re.I),
    "cpu": re.compile(r"\b(?:ryzen\s*[3579]|core\s*i[3579][\s-]?\d{4,5}|i[3579][\s-]?\d{4,5})\b", re.I),
    "console": re.compile(
        r"\b(?:ps[1-5]|playstation\s*[1-5]|xbox(?:\s*(?:360|one|series\s*[xs]))?|"
        r"switch(?:\s*(?:2|oled|lite))?|steam\s*deck|rog\s*ally)\b",
        re.I,
    ),
    "phone": re.compile(r"\b(?:iphone\s*\d{1,2}|galaxy\s*[sanz]\d{1,2}|pixel\s*\d{1,2})\b", re.I),
    "tablet": re.compile(r"\b(?:ipad(?:\s*(?:pro|air|mini))?|surface\s*pro|galaxy\s*tab)\b", re.I),
    "laptop": re.compile(r"\b(?:macbook|thinkpad|latitude|elitebook|surface\s*laptop|notebook|laptop)\b", re.I),
    "monitor": re.compile(r"\b(?:monitor|display)\b", re.I),
    "ssd": re.compile(r"\b(?:ssd|nvme|hard\s*drive|hdd)\b", re.I),
    "ram": re.compile(r"\b(?:ddr[345]|ram|memory)\b", re.I),
    "motherboard": re.compile(r"\b(?:motherboard|mainboard|b450|b550|x570|z690|z790|b650|b760)\b", re.I),
}
DEVICE_NOUNS = {
    "gpu": re.compile(r"\b(?:graphics\s*card|gpu|geforce|radeon)\b", re.I),
    "cpu": re.compile(r"\b(?:processor|cpu)\b", re.I),
    "console": re.compile(r"\b(?:console|handheld)\b", re.I),
    "phone": re.compile(r"\b(?:phone|smartphone|mobile)\b", re.I),
    "tablet": re.compile(r"\b(?:tablet|ipad)\b", re.I),
    "laptop": re.compile(r"\b(?:laptop|notebook|macbook)\b", re.I),
    "monitor": re.compile(r"\b(?:monitor)\b", re.I),
    "ssd": re.compile(r"\b(?:ssd|drive)\b", re.I),
    "ram": re.compile(r"\b(?:ram|memory)\b", re.I),
    "motherboard": re.compile(r"\b(?:motherboard|mainboard)\b", re.I),
}
STRONG_NEGATIVE = re.compile(
    r"\b(?:empty box|box only|packaging only|spares? or repairs?|for parts|"
    r"not working|faulty|wanted|looking to buy|wtb)\b",
    re.I,
)
GAME_ONLY = re.compile(r"\b(?:game|games|cartridge|disc only|pegi|esrb|dlc)\b", re.I)


def oracle(title: str) -> tuple[str, bool | None, str]:
    """Coarse, independent expectation: (kind, valued, label_status)."""
    text = " ".join((title or "").split())
    # "Xbox games console" is a console, not a game. Remove the phrase before
    # looking for game wording so the oracle does not invert the label.
    text = re.sub(r"\bgames?\s+console\b", "console", text, flags=re.I)
    if not text:
        return "unknown", None, "unverified"
    negative = bool(ACCESSORY_RE.search(text)) or bool(STRONG_NEGATIVE.search(text))
    matches = [kind for kind, pattern in MODEL_RES.items() if pattern.search(text)]
    if len(matches) != 1:
        # Two product classes in one title (a PC with a GPU, a bundle) is
        # ambiguous for this oracle; leave it unverified.
        return "unknown", None, "unverified"
    kind = matches[0]
    if negative:
        # An accessory/game title that names a device is a clear false-positive
        # example only when the accessory wording is not itself the product.
        if GAME_ONLY.search(text) or STRONG_NEGATIVE.search(text):
            return kind, False, "verified"
        return kind, None, "unverified"
    if DEVICE_NOUNS[kind].search(text) or kind in {"phone", "tablet", "laptop"}:
        return kind, True, "verified"
    return kind, None, "unverified"


def sample(db: Database, size: int, seed: int) -> list[dict]:
    rows = db.query(
        """
        SELECT id, marketplace, title, price, condition_norm, product_key,
               listing_kind, identity_level, identification_confidence, category
        FROM listings
        WHERE status='active' AND last_seen >= datetime('now', '-10 days')
          AND title IS NOT NULL AND length(title) > 5
        """
    )
    rng = random.Random(seed)
    by_stratum: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        price = float(row["price"] or 0)
        band = "low" if price < 60 else "mid" if price < 300 else "high"
        by_stratum[(row["marketplace"], row["listing_kind"] or "none", band)].append(row)
    strata = sorted(by_stratum)
    chosen: list[dict] = []
    # Round-robin across strata so marketplaces, kinds and price bands all
    # appear; the last few are filled from the largest strata if needed.
    pools = {key: rng.sample(value, len(value)) for key, value in by_stratum.items()}
    while len(chosen) < size and any(pools.values()):
        for key in strata:
            if len(chosen) >= size:
                break
            if pools[key]:
                chosen.append(pools[key].pop())
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=240)
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--write", action="store_true", help="write the frozen fixture")
    args = parser.parse_args()

    db = Database(settings.db_path)
    rows = sample(db, args.sample, args.seed)
    overrides = {}
    if OVERRIDES.exists():
        overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")).get("overrides", {})
    results = []
    for row in rows:
        ident = identify_listing(row["title"])
        expected_kind, expected_valued, status = oracle(row["title"])
        override = overrides.get(str(row["id"]))
        if override:
            expected_valued = bool(override["expected_valued"])
            status = "verified"
        results.append(
            {
                "id": row["id"],
                "marketplace": row["marketplace"],
                "title": row["title"],
                "price": round(float(row["price"] or 0), 2),
                "condition": row["condition_norm"],
                "predicted_kind": ident.kind,
                "predicted_key": ident.product_key,
                "predicted_valued": bool(ident.product_key) and not ident.is_bundle,
                "predicted_bundle": bool(ident.is_bundle),
                "expected_kind": expected_kind,
                "expected_valued": expected_valued,
                "label_status": status,
                "label_source": "manual" if override else "oracle",
                "label_note": (override or {}).get("reason"),
            }
        )
    db.close()

    verified_kind = [
        r for r in results
        if r["label_status"] == "verified"
        and r["expected_kind"] != "unknown"
        and r["expected_valued"] is True
    ]
    kind_correct = [
        r for r in verified_kind if r["predicted_kind"] == r["expected_kind"]
    ]
    verified_value = [
        r for r in results
        if r["label_status"] == "verified" and r["expected_valued"] is not None
    ]
    false_positives = [
        r for r in verified_value
        if r["expected_valued"] is False and r["predicted_valued"]
    ]
    false_negatives = [
        r for r in verified_value
        if r["expected_valued"] is True and not r["predicted_valued"]
    ]
    true_positives = [
        r for r in verified_value
        if r["expected_valued"] is True and r["predicted_valued"]
    ]
    true_negatives = [
        r for r in verified_value
        if r["expected_valued"] is False and not r["predicted_valued"]
    ]
    unverified = sum(1 for r in results if r["label_status"] != "verified")

    def rate(numerator: int, denominator: int) -> str:
        return f"{numerator}/{denominator} ({numerator / denominator:.1%})" if denominator else "n/a"

    print(f"sample: {len(results)} listings, seed {args.seed}")
    print(f"oracle-verified: {len(results) - unverified}; unverified: {unverified}")
    print(
        "kind accuracy:",
        rate(len(kind_correct), len(verified_kind)),
        "| confusion:",
        dict(Counter((r["expected_kind"], r["predicted_kind"]) for r in verified_kind if r["predicted_kind"] != r["expected_kind"])),
    )
    print(
        "valued decision:",
        "precision", rate(len(true_positives), len(true_positives) + len(false_positives)),
        "recall", rate(len(true_positives), len(true_positives) + len(false_negatives)),
    )
    print(
        "false positives:", len(false_positives),
        "| false negatives:", len(false_negatives),
        "| true values:", len(true_positives), "+", len(true_negatives),
    )
    print("by marketplace:", dict(Counter(r["marketplace"] for r in results)))
    print("by predicted kind:", dict(Counter(r["predicted_kind"] for r in results)))
    for label, items in (("FP", false_positives), ("FN", false_negatives)):
        for row in items[:8]:
            print(f"  {label} [{row['marketplace']}] {row['title'][:100]!r} -> {row['predicted_kind']} {row['predicted_key']}")

    if args.write:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(
            json.dumps(
                {
                    "seed": args.seed,
                    "sample_size": len(results),
                    "generated_from": "data/rigradar.db",
                    "note": (
                        "Labels come from the independent coarse oracle in "
                        "scripts/benchmark_listings.py; unverified rows are ambiguous "
                        "and excluded from accuracy denominators."
                    ),
                    "listings": results,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print("wrote", FIXTURE.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
