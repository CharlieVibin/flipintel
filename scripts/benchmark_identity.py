"""Compare the previous classifier with the current one on real listings.

The previous module is read from Git (``HEAD:app/identification.py``) so the
comparison is apples-to-apples. The labelled dataset is
``tests/fixtures/identity_regression.json``: titles collected from the live
database with the identity class, key prefix, condition and confidence range
each one must produce.

    .venv/Scripts/python scripts/benchmark_identity.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.identification import identify_listing as current  # noqa: E402

DATASET = json.loads(
    (ROOT / "tests" / "fixtures" / "identity_regression.json").read_text(encoding="utf-8")
)


def load_previous():
    source = subprocess.run(
        ["git", "show", "HEAD:app/identification.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    path = Path(tempfile.mkstemp(suffix=".py")[1])
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("app.identification_previous", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.identify_listing


def passes(result, case) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if case.get("no_key") and result.product_key is not None:
        failures.append(f"key={result.product_key}")
    if case.get("kind") and result.kind != case["kind"]:
        failures.append(f"kind={result.kind}")
    if case.get("key_prefix") and not (result.product_key or "").startswith(case["key_prefix"]):
        failures.append(f"key={result.product_key}")
    if case.get("condition") and result.condition != case["condition"]:
        failures.append(f"condition={result.condition}")
    if "is_faulty" in case and result.is_faulty is not case["is_faulty"]:
        failures.append(f"faulty={result.is_faulty}")
    for flag in case.get("flags_include", []):
        if flag not in result.flags:
            failures.append(f"missing flag {flag}")
    if "max_confidence" in case and result.confidence > case["max_confidence"]:
        failures.append(f"confidence={result.confidence:.2f}")
    if "min_confidence" in case and result.confidence < case["min_confidence"]:
        failures.append(f"confidence={result.confidence:.2f}")
    return not failures, failures


def main() -> None:
    previous = load_previous()
    old_pass = new_pass = 0
    rows = []
    for case in DATASET:
        old = previous(case["title"])
        new = current(case["title"])
        old_ok, old_fail = passes(old, case)
        new_ok, new_fail = passes(new, case)
        old_pass += old_ok
        new_pass += new_ok
        rows.append(
            {
                "title": case["title"][:72],
                "old": "pass" if old_ok else f"FAIL ({', '.join(old_fail)})",
                "new": "pass" if new_ok else f"FAIL ({', '.join(new_fail)})",
                "old_key": old.product_key,
                "new_key": new.product_key,
            }
        )
    print(json.dumps(rows, indent=2))
    total = len(DATASET)
    print(
        json.dumps(
            {
                "cases": total,
                "previous_accuracy": round(old_pass / total, 3),
                "current_accuracy": round(new_pass / total, 3),
                "previous_passes": old_pass,
                "current_passes": new_pass,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
