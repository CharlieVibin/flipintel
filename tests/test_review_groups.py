"""Grouped review and explicit bulk-decision regressions."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from app.utils import iso


def _pending_phone(
    db: Database,
    external_id: str,
    *,
    marketplace: str = "vinted",
    key: str = "phone:iphone-13",
    name: str = "Apple iPhone 13",
    flags: list[str] | None = None,
    price: float = 300,
    decision: str | None = None,
) -> int:
    now = iso()
    listing_id = db.execute(
        """
        INSERT INTO listings(
            marketplace, external_id, url, title, price, condition_norm,
            listing_kind, category, product_key, product_name,
            identification_confidence, flags, attrs,
            first_seen, last_seen, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'good', 'phone', 'phone', ?, ?, 0.9, ?, '{}', ?, ?, 'active', ?, ?)
        """,
        (
            marketplace,
            external_id,
            f"https://www.{marketplace}.co.uk/item/{external_id}",
            f"{name} {external_id}",
            price,
            key,
            name,
            json.dumps(flags if flags is not None else ["variant_unclear"]),
            now,
            now,
            now,
            now,
        ),
    ).lastrowid
    db.execute(
        "INSERT INTO deals(listing_id, needs_review, review_reason, eligibility, computed_at) "
        "VALUES (?, 1, ?, 'needs_verification', ?)",
        (listing_id, "Storage, memory or chip variant is not stated", now),
    )
    if decision:
        db.execute(
            "INSERT INTO review_decisions(listing_id, decision, note, decided_at) "
            "VALUES (?, ?, 'fixture', ?)",
            (listing_id, decision, now),
        )
    return int(listing_id)


def _pending_fixture(db: Database) -> dict[str, int]:
    ids = {
        "13a": _pending_phone(db, "13a", price=240),
        "13b": _pending_phone(db, "13b", marketplace="gumtree", price=310),
        "14a": _pending_phone(
            db,
            "14a",
            key="phone:iphone-14",
            name="Apple iPhone 14",
            price=380,
        ),
    }
    # Already decided and non-variant rows must not leak into the group.
    ids["decided"] = _pending_phone(db, "decided", price=250, decision="rejected")
    ids["known_storage"] = _pending_phone(db, "known-storage", flags=[], price=270)
    return ids


def test_variant_reviews_are_grouped_by_model_and_filterable(tmp_path):
    db = Database(tmp_path / "review-groups.db")
    try:
        ids = _pending_fixture(db)
        app = create_app(db=db, autostart=False)
        with TestClient(app) as client:
            response = client.get("/api/reviews/groups?kind=phone&flag=variant_unclear")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["total_groups"] == 2
            assert body["total_listings"] == 3
            assert body["label"] == "Variant not stated"
            assert [group["count"] for group in body["groups"]] == [2, 1]

            iphone13 = body["groups"][0]
            assert iphone13["product_key"] == "phone:iphone-13"
            assert iphone13["product_name"] == "Apple iPhone 13"
            assert iphone13["marketplaces"] == ["gumtree", "vinted"]
            assert iphone13["price_low"] == 240
            assert iphone13["price_high"] == 310
            assert sorted(iphone13["listing_ids"]) == sorted([ids["13a"], ids["13b"]])
            assert len(iphone13["examples"]) == 2
            assert all(example["url"].startswith("https://") for example in iphone13["examples"])

            filtered = client.get(
                "/api/deals?review=1&product_key=phone:iphone-13&review_flag=variant_unclear&limit=20"
            )
            assert filtered.status_code == 200
            assert filtered.json()["total"] == 2
            assert {deal["id"] for deal in filtered.json()["deals"]} == {
                ids["13a"], ids["13b"]
            }
            # The same model has one pending price review with known storage;
            # it must stay out of the variant-gap view.
            model_only = client.get(
                "/api/deals?review=1&product_key=phone:iphone-13&limit=20"
            )
            assert model_only.json()["total"] == 3
            assert client.get("/api/deals?review=1&review_flag=nope").status_code == 422

            assert client.get("/api/reviews/groups?kind=nope").status_code == 422
            assert client.get("/api/reviews/groups?flag=nope").status_code == 422
    finally:
        db.close()


def test_bulk_review_updates_only_pending_ids_and_is_idempotent(tmp_path):
    db = Database(tmp_path / "review-bulk.db")
    try:
        ids = _pending_fixture(db)
        app = create_app(db=db, autostart=False)
        with TestClient(app) as client:
            body = client.get("/api/reviews/groups").json()
            group_ids = body["groups"][0]["listing_ids"]
            assert sorted(group_ids) == sorted([ids["13a"], ids["13b"]])

            rejected = client.post(
                "/api/reviews/bulk",
                json={"decision": "rejected", "listing_ids": group_ids},
            )
            assert rejected.status_code == 200, rejected.text
            result = rejected.json()
            assert result["updated"] == 2
            assert result["skipped"] == 0
            assert result["remaining"] == 2

            history = client.get(
                "/api/deals?review=1&review_status=rejected&product_key=phone:iphone-13"
                "&review_flag=variant_unclear"
            ).json()
            # Includes the previously decided fixture plus both new rejections.
            assert history["total"] == 3
            assert all(
                deal["review_decision"] == "rejected" for deal in history["deals"]
            )

            # Repeating the action cannot overwrite a decision or inflate counts.
            repeated = client.post(
                "/api/reviews/bulk",
                json={"decision": "approved", "listing_ids": group_ids},
            ).json()
            assert repeated["updated"] == 0
            assert repeated["skipped"] == 2
            assert repeated["remaining"] == 2

            approved = client.post(
                "/api/reviews/bulk",
                json={"decision": "approved", "listing_ids": [ids["14a"]]},
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["updated"] == 1
            detail = client.get(f"/api/deals/{ids['14a']}").json()
            assert detail["review_decision"] == "approved"
            assert detail["needs_review"] is False

            assert client.post(
                "/api/reviews/bulk", json={"decision": "corrected", "listing_ids": [ids["13a"]]}
            ).status_code == 422
            assert client.post(
                "/api/reviews/bulk", json={"decision": "rejected", "listing_ids": []}
            ).status_code == 422
            assert client.post(
                "/api/reviews/bulk",
                json={"decision": "rejected", "listing_ids": list(range(1, 202))},
            ).status_code == 422
    finally:
        db.close()
