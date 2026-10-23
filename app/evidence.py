"""Completed-sale evidence import, export and validation.

CSV evidence is user-supplied. It is useful, but it is never treated as an
independently verified marketplace sale. Every row is validated, provenance is
retained, and re-importing the same sale updates rather than duplicates it.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from .db import Database
from .identification import CONDITION_FACTORS
from .utils import iso, normalise_text, parse_money, utcnow

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 2_000
MAX_PREVIEW_ROWS = 300
MAX_TITLE = 500
MAX_NOTES = 1000
MAX_VARIANTS = 1000
MAX_URL = 2000

TEMPLATE_FIELDS = (
    "product_key",
    "product_name",
    "title",
    "marketplace",
    "sold_price",
    "postage",
    "condition",
    "sale_date",
    "currency",
    "kind",
    "storage_gb",
    "variants",
    "url",
    "notes",
    "source_id",
)

FIELD_ALIASES = {
    "product": "product_key",
    "productkey": "product_key",
    "product_id": "product_key",
    "model": "product_key",
    "product_name": "product_name",
    "item": "title",
    "item_title": "title",
    "listing_title": "title",
    "soldprice": "sold_price",
    "sale_price": "sold_price",
    "price": "sold_price",
    "sold_for": "sold_price",
    "delivery": "postage",
    "shipping": "postage",
    "postage_cost": "postage",
    "condition": "condition",
    "sold_date": "sale_date",
    "date": "sale_date",
    "date_sold": "sale_date",
    "listing_url": "url",
    "link": "url",
    "note": "notes",
    "storage": "storage_gb",
    "capacity_gb": "storage_gb",
    "variant": "variants",
    "hardware_variants": "variants",
    "external_id": "source_id",
    "sale_id": "source_id",
}

PRODUCT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:+-]{1,119}$")
KIND_FROM_PREFIX = {
    "gpu": "gpu",
    "cpu": "cpu",
    "ram": "ram",
    "ssd": "ssd",
    "hdd": "ssd",
    "mobo": "motherboard",
    "motherboard": "motherboard",
    "pc": "pc",
    "laptop": "laptop",
    "console": "console",
    "monitor": "monitor",
    "phone": "phone",
    "tablet": "tablet",
}
VALID_KINDS = {"gpu", "cpu", "ram", "ssd", "motherboard", "pc", "laptop", "console", "monitor", "phone", "tablet", "networking"}
SUPPORTED_CURRENCIES = {"GBP", "£"}


def template_csv() -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=TEMPLATE_FIELDS, lineterminator="\r\n")
    writer.writeheader()
    return output.getvalue()


def _csv_safe(value: Any) -> str:
    text = str(value or "")
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + text
    return text


def export_csv(rows: list[dict[str, Any]]) -> str:
    """Export saved evidence with enough provenance to be re-importable."""
    fields = list(TEMPLATE_FIELDS) + ["evidence_type", "verified", "evidence_confidence", "imported_at"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\r\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        variants = row.get("variants") or ""
        if variants and not isinstance(variants, str):
            variants = str(variants)
        writer.writerow(
            {
                "product_key": _csv_safe(row.get("product_key")),
                "product_name": _csv_safe(row.get("product_name")),
                "title": _csv_safe(row.get("title")),
                "marketplace": _csv_safe(row.get("marketplace") or "manual"),
                "sold_price": f"{float(row.get('price') or 0):.2f}",
                "postage": f"{float(row.get('postage') or 0):.2f}",
                "condition": row.get("condition_norm") or "unknown",
                "sale_date": (row.get("observed_at") or "")[:10],
                "currency": row.get("currency") or "GBP",
                "kind": row.get("kind") or "",
                "storage_gb": row.get("storage_gb") or "",
                "variants": _csv_safe(variants),
                "url": _csv_safe(row.get("url")),
                "notes": _csv_safe(row.get("notes")),
                "source_id": _csv_safe(row.get("source_id")),
                "evidence_type": evidence_type(row),
                "verified": "yes" if row.get("verified") else "no",
                "evidence_confidence": row.get("evidence_confidence") or "",
                "imported_at": row.get("created_at") or "",
            }
        )
    return output.getvalue()


def evidence_type(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "").lower()
    if source == "manual_csv":
        return "user_imported"
    if source == "manual":
        return "user_entered"
    if source == "cex":
        return "retail_anchor"
    if row.get("is_sold"):
        return "marketplace_sale"
    return "marketplace_asking"


def infer_kind(product_key: str, supplied: str = "") -> str | None:
    kind = normalise_text(supplied).lower()
    if kind in VALID_KINDS:
        return kind
    prefix = product_key.split(":", 1)[0].lower()
    return KIND_FROM_PREFIX.get(prefix)


def _parse_sale_date(raw: str, *, row_number: int) -> tuple[str, list[str]]:
    text = normalise_text(raw)
    if not text:
        return iso(), [f"Row {row_number}: sale_date is missing; the import date was used."]
    candidates = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %b %Y",
        "%d %B %Y",
    ]
    parsed: datetime | None = None
    for pattern in candidates:
        try:
            parsed = datetime.strptime(text[:19], pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("sale_date must be YYYY-MM-DD or DD/MM/YYYY")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed > utcnow() + timedelta(days=1):
        raise ValueError("sale_date cannot be in the future")
    if parsed < datetime(2000, 1, 1, tzinfo=timezone.utc):
        raise ValueError("sale_date is implausibly old")
    warnings = []
    if parsed.year < utcnow().year - 2:
        warnings.append(f"Row {row_number}: sale is more than two years old; it carries little calibration weight.")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds"), warnings


def _validate_url(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    if len(text) > MAX_URL:
        raise ValueError("url is too long")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an http(s) link")
    if parsed.username or parsed.password:
        raise ValueError("url must not contain embedded credentials")
    return text


def _validate_row(
    raw: dict[str, Any],
    row_number: int,
    *,
    import_date: str,
) -> dict[str, Any]:
    def value(field: str) -> str:
        return normalise_text(str(raw.get(field) or ""))

    product_key = value("product_key").lower()
    if not product_key:
        raise ValueError("product_key is required")
    if not PRODUCT_KEY_RE.match(product_key):
        raise ValueError("product_key must be a lowercase identifier such as console:ps5 or gpu:rtx-4070")
    title = value("title")[:MAX_TITLE]
    if len(title) < 3:
        raise ValueError("title is required (at least 3 characters)")

    currency = value("currency").upper() or "GBP"
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("only GBP prices are supported")
    price = parse_money(value("sold_price"))
    if price is None or price <= 0:
        raise ValueError("sold_price must be a positive GBP amount")
    if price > 100_000:
        raise ValueError("sold_price is implausibly high")
    postage_raw = value("postage")
    postage = parse_money(postage_raw) if postage_raw else 0.0
    if postage is None or postage < 0 or postage > 500:
        raise ValueError("postage must be between 0 and 500 GBP")

    condition = value("condition").lower().replace("-", "_").replace(" ", "_") or "unknown"
    condition_alias = {
        "brand_new": "new",
        "new_sealed": "new",
        "opened": "open_box",
        "openbox": "open_box",
        "like_new": "like_new",
        "mint": "like_new",
        "verygood": "very_good",
        "very_good": "very_good",
        "used": "used",
        "working": "good",
        "for_parts": "faulty",
        "spares": "faulty",
    }
    condition = condition_alias.get(condition, condition)
    if condition not in CONDITION_FACTORS:
        raise ValueError(f"unknown condition '{condition}'")

    observed_at, warnings = _parse_sale_date(value("sale_date"), row_number=row_number)
    kind = infer_kind(product_key, value("kind"))
    if not kind:
        raise ValueError("kind could not be inferred; supply a valid kind column")
    url = _validate_url(value("url"))
    notes = value("notes")[:MAX_NOTES]
    variants = value("variants")[:MAX_VARIANTS]
    storage_raw = value("storage_gb").lower()
    storage_gb: int | None = None
    if storage_raw:
        match = re.search(r"(\d+(?:\.\d+)?)", storage_raw)
        if not match:
            raise ValueError("storage_gb must be a number")
        storage = float(match.group(1))
        if "tb" in storage_raw:
            storage *= 1024
        if not (8 <= storage <= 100_000):
            raise ValueError("storage_gb is outside the supported range")
        storage_gb = int(storage)
        variants = f"storage={storage_gb}GB" + (f"; {variants}" if variants else "")

    source_id_raw = value("source_id")
    if source_id_raw:
        if len(source_id_raw) > 200 or not re.match(r"^[A-Za-z0-9._:@/+ -]{1,200}$", source_id_raw):
            raise ValueError("source_id contains unsupported characters")
        source_id = source_id_raw
    else:
        # Stable across re-imports. The raw date text is used so a missing date
        # remains "undated" rather than becoming a different sale each day.
        date_token = observed_at[:10] if normalise_text(str(raw.get("sale_date") or "")) else "undated"
        # The natural key excludes price so correcting a recorded price in a
        # later CSV updates that sale instead of creating a duplicate.
        fingerprint = "|".join(
            (
                product_key,
                title.lower(),
                condition,
                value("marketplace").lower() or "manual",
                date_token,
            )
        )
        source_id = "csv-" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:24]

    return {
        "product_key": product_key,
        "product_name": value("product_name")[:200] or None,
        "title": title,
        "marketplace": value("marketplace")[:80] or "manual",
        "price": round(price, 2),
        "postage": round(postage, 2),
        "condition_norm": condition,
        "condition_raw": value("condition")[:120] or None,
        "observed_at": observed_at,
        "currency": "GBP",
        "kind": kind,
        "storage_gb": storage_gb,
        "variants": variants or None,
        "url": url,
        "notes": notes or None,
        "source_id": source_id,
        "evidence_confidence": 0.68,
        "verified": 0,
        "warnings": warnings,
        "import_date": import_date,
        "_row": row_number,
    }


def _read_csv(content: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    if len(content) > MAX_CSV_BYTES:
        raise ValueError(f"CSV is larger than the {MAX_CSV_BYTES // (1024 * 1024)} MB limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV must be UTF-8 or Windows-1252 text") from exc
    if not text.strip():
        raise ValueError("CSV file is empty")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    renamed: dict[str, str] = {}
    for header in reader.fieldnames:
        normal = normalise_text(header).lower().replace(" ", "_").replace("-", "_")
        canonical = FIELD_ALIASES.get(normal, normal)
        renamed[header] = canonical
    canonical_headers = set(renamed.values())
    required = {"product_key", "title", "sold_price"}
    missing = sorted(required - canonical_headers)
    if missing:
        raise ValueError(f"CSV is missing required column(s): {', '.join(missing)}")
    warnings: list[str] = []
    if "sale_date" not in canonical_headers:
        warnings.append("sale_date column is absent; the import date will be used for every row.")
    if "condition" not in canonical_headers:
        warnings.append("condition column is absent; rows will be recorded as condition unknown.")
    rows: list[dict[str, Any]] = []
    for index, original in enumerate(reader, start=2):
        if index - 1 > MAX_CSV_ROWS:
            raise ValueError(f"CSV exceeds the {MAX_CSV_ROWS} row limit")
        if not any(normalise_text(str(value or "")) for value in original.values()):
            continue
        mapped: dict[str, Any] = {}
        for header, raw_value in original.items():
            if header is None:
                continue
            mapped[renamed.get(header, header)] = raw_value
        mapped["_row"] = index
        rows.append(mapped)
    if not rows:
        raise ValueError("CSV contains no data rows")
    return rows, warnings


def preview_import(
    db: Database,
    content: bytes,
    filename: str = "evidence.csv",
    *,
    commit: bool = False,
) -> dict[str, Any]:
    """Validate and optionally persist a CSV import.

    Preview mode performs the same validation as commit but writes nothing.
    """
    parsed_rows, file_warnings = _read_csv(content)
    import_date = iso()
    prepared: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in parsed_rows:
        row_number = int(raw.get("_row") or 0)
        try:
            row = _validate_row(raw, row_number, import_date=import_date)
        except ValueError as exc:
            rejected.append({"row": row_number, "reason": str(exc)})
            continue
        if row["source_id"] in seen:
            prepared.append({**row, "action": "skipped", "reason": "duplicate within this CSV"})
            continue
        seen.add(row["source_id"])
        existing = db.query_one(
            "SELECT * FROM market_comps WHERE source='manual_csv' AND source_id=?",
            (row["source_id"],),
        )
        manual_twin = None
        if not existing:
            manual_twin = db.query_one(
                """
                SELECT id, source FROM market_comps
                WHERE is_sold=1 AND source IN ('manual', 'manual_csv')
                  AND product_key=? AND lower(title)=lower(?)
                  AND abs(price - ?) < 0.005
                  AND substr(observed_at, 1, 10)=substr(?, 1, 10)
                """,
                (row["product_key"], row["title"], row["price"], row["observed_at"]),
            )
        if manual_twin:
            prepared.append({**row, "action": "skipped", "reason": "a matching user-supplied sale already exists"})
            continue
        if existing:
            comparable = (
                existing.get("product_key") == row["product_key"]
                and existing.get("title") == row["title"]
                and abs(float(existing.get("price") or 0) - row["price"]) < 0.005
                and abs(float(existing.get("postage") or 0) - row["postage"]) < 0.005
                and existing.get("condition_norm") == row["condition_norm"]
                and (existing.get("notes") or None) == row["notes"]
                and (existing.get("url") or None) == row["url"]
                and (existing.get("variants") or None) == row["variants"]
                and (existing.get("observed_at") or "")[:10] == row["observed_at"][:10]
            )
            if comparable:
                prepared.append({**row, "action": "skipped", "reason": "identical record already exists"})
            else:
                prepared.append({**row, "action": "updated", "reason": "existing record updated", "id": existing["id"]})
        else:
            prepared.append({**row, "action": "imported", "reason": ""})

    counts = {key: sum(1 for row in prepared if row["action"] == key) for key in ("imported", "updated", "skipped")}
    counts["rejected"] = len(rejected)
    import_id: int | None = None
    if commit:
        digest = hashlib.sha256(content).hexdigest()
        cursor = db.execute(
            "INSERT INTO evidence_imports(filename, created_at, file_sha256, total_rows, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (filename[:200], iso(), digest, len(parsed_rows), "{}"),
        )
        import_id = int(cursor.lastrowid)
        rows_to_write = [row for row in prepared if row["action"] in {"imported", "updated"}]
        if rows_to_write:
            db.executemany(
                """
                INSERT INTO market_comps(product_key, marketplace, source, source_id, title, price,
                    kind, condition_norm, is_sold, postage, notes, verified, evidence_confidence,
                    url, observed_at, created_at, currency, product_name, condition_raw,
                    variants, import_id, updated_at)
                VALUES (?, ?, 'manual_csv', ?, ?, ?, ?, ?, 1, ?, ?, 0, ?, ?, ?, ?, 'GBP', ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    product_key=excluded.product_key, marketplace=excluded.marketplace,
                    title=excluded.title, price=excluded.price, kind=excluded.kind,
                    condition_norm=excluded.condition_norm, postage=excluded.postage,
                    notes=excluded.notes, evidence_confidence=excluded.evidence_confidence,
                    url=excluded.url, observed_at=excluded.observed_at,
                    product_name=excluded.product_name, condition_raw=excluded.condition_raw,
                    variants=excluded.variants, import_id=excluded.import_id,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        row["product_key"], row["marketplace"], row["source_id"], row["title"],
                        row["price"], row["kind"], row["condition_norm"], row["postage"],
                        row["notes"], row["evidence_confidence"], row["url"], row["observed_at"],
                        import_date, row["product_name"], row["condition_raw"], row["variants"],
                        import_id, import_date,
                    )
                    for row in rows_to_write
                ],
            )
        db.execute(
            "UPDATE evidence_imports SET imported=?, updated=?, skipped=?, rejected=?, detail=? WHERE id=?",
            (
                counts["imported"], counts["updated"], counts["skipped"], counts["rejected"],
                json.dumps({"rejected": rejected[:100], "warnings": file_warnings}),
                import_id,
            ),
        )

    public_rows = []
    for row in prepared[:MAX_PREVIEW_ROWS]:
        public_rows.append(
            {
                "row": row["_row"],
                "action": row["action"],
                "reason": row["reason"],
                "warnings": row["warnings"],
                "product_key": row["product_key"],
                "product_name": row["product_name"],
                "title": row["title"],
                "marketplace": row["marketplace"],
                "price": row["price"],
                "postage": row["postage"],
                "condition": row["condition_norm"],
                "observed_at": row["observed_at"],
                "currency": row["currency"],
                "kind": row["kind"],
                "source_id": row["source_id"],
            }
        )
    return {
        "filename": filename,
        "commit": commit,
        "import_id": import_id,
        "total_rows": len(parsed_rows),
        "counts": counts,
        "rows": public_rows,
        "rejected": rejected[:MAX_PREVIEW_ROWS],
        "warnings": file_warnings,
        "truncated": len(prepared) > MAX_PREVIEW_ROWS,
        "limits": {"max_bytes": MAX_CSV_BYTES, "max_rows": MAX_CSV_ROWS},
    }


def validate_comp_update(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a correction to an existing user-supplied comparable."""
    updates: dict[str, Any] = {}
    if "product_key" in payload and payload["product_key"] is not None:
        key = normalise_text(str(payload["product_key"])).lower()
        if not PRODUCT_KEY_RE.match(key):
            raise ValueError("product_key is not valid")
        updates["product_key"] = key
    if "title" in payload and payload["title"] is not None:
        title = normalise_text(str(payload["title"]))[:MAX_TITLE]
        if len(title) < 3:
            raise ValueError("title is too short")
        updates["title"] = title
    if "price" in payload and payload["price"] is not None:
        price = parse_money(payload["price"])
        if price is None or price <= 0 or price > 100_000:
            raise ValueError("price must be a positive GBP amount")
        updates["price"] = round(price, 2)
    if "postage" in payload and payload["postage"] is not None:
        postage = parse_money(payload["postage"])
        if postage is None or postage < 0 or postage > 500:
            raise ValueError("postage must be between 0 and 500 GBP")
        updates["postage"] = round(postage, 2)
    if "condition" in payload and payload["condition"] is not None:
        condition = normalise_text(str(payload["condition"])).lower().replace(" ", "_")
        if condition not in CONDITION_FACTORS:
            raise ValueError("unknown condition")
        updates["condition_norm"] = condition
    if "kind" in payload and payload["kind"] is not None:
        kind = normalise_text(str(payload["kind"])).lower()
        if kind not in VALID_KINDS:
            raise ValueError("unknown kind")
        updates["kind"] = kind
    if "notes" in payload and payload["notes"] is not None:
        updates["notes"] = normalise_text(str(payload["notes"]))[:MAX_NOTES] or None
    if "url" in payload and payload["url"] is not None:
        updates["url"] = _validate_url(str(payload["url"]))
    if "observed_at" in payload and payload["observed_at"] is not None:
        observed, _ = _parse_sale_date(str(payload["observed_at"]), row_number=0)
        updates["observed_at"] = observed
    if not updates:
        raise ValueError("no fields to update")
    return updates


def evidence_rows(
    db: Database,
    *,
    product_key: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    where = ["is_sold = 1"]
    params: list[Any] = []
    if product_key:
        where.append("product_key = ?")
        params.append(product_key.strip().lower())
    if source:
        if source == "user":
            where.append("source IN ('manual', 'manual_csv')")
        elif source == "verified":
            where.append("verified = 1")
        else:
            where.append("source = ?")
            params.append(source)
    if date_from:
        where.append("observed_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("observed_at <= ?")
        params.append(date_to + "T23:59:59+00:00")
    if q:
        where.append("(title LIKE ? OR product_key LIKE ? OR notes LIKE ?)")
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    where_sql = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM market_comps WHERE {where_sql}", params) or 0
    rows = db.query(
        f"""
        SELECT id, product_key, product_name, title, marketplace, source, source_id, price,
               postage, kind, condition_norm, condition_raw, is_sold, notes, verified,
               evidence_confidence, url, observed_at, created_at, updated_at, currency,
               variants, import_id
        FROM market_comps WHERE {where_sql}
        ORDER BY observed_at DESC, id DESC LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    )
    for row in rows:
        row["evidence_type"] = evidence_type(row)
        row["verified"] = bool(row.get("verified"))
        row["price"] = round(float(row.get("price") or 0), 2)
        row["postage"] = round(float(row.get("postage") or 0), 2)
    return {"evidence": rows, "total": total, "limit": limit, "offset": offset}


def evidence_coverage(db: Database) -> dict[str, Any]:
    rows = db.query(
        """
        SELECT product_key,
               MAX(product_name) AS product_name,
               MAX(kind) AS kind,
               COUNT(*) AS sold_count,
               SUM(CASE WHEN source IN ('manual', 'manual_csv') THEN 1 ELSE 0 END) AS user_count,
               SUM(CASE WHEN source NOT IN ('manual', 'manual_csv') THEN 1 ELSE 0 END) AS independent_count,
               SUM(CASE WHEN source = 'manual_csv' THEN 1 ELSE 0 END) AS imported_count,
               MIN(price) AS min_price,
               MAX(price) AS max_price,
               MAX(observed_at) AS last_sale_at
        FROM market_comps
        WHERE is_sold = 1
        GROUP BY product_key
        ORDER BY sold_count DESC, last_sale_at DESC
        """
    )
    for row in rows:
        count = int(row["sold_count"] or 0)
        row["evidence_level"] = "strong" if count >= 3 else "moderate" if count >= 2 else "thin"
        row["usable_for_calibration"] = bool(count >= 2 and int(row["independent_count"] or 0) >= 2)
        row["min_price"] = round(float(row["min_price"] or 0), 2)
        row["max_price"] = round(float(row["max_price"] or 0), 2)
    imports = db.query(
        "SELECT id, filename, created_at, total_rows, imported, updated, skipped, rejected "
        "FROM evidence_imports ORDER BY id DESC LIMIT 20"
    )
    totals = db.query_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN source IN ('manual', 'manual_csv') THEN 1 ELSE 0 END) AS user_supplied,
               SUM(CASE WHEN source NOT IN ('manual', 'manual_csv') THEN 1 ELSE 0 END) AS independent,
               COUNT(DISTINCT product_key) AS products
        FROM market_comps WHERE is_sold = 1
        """
    ) or {}
    return {
        "products": rows,
        "imports": imports,
        "totals": {
            "total": totals.get("total") or 0,
            "user_supplied": totals.get("user_supplied") or 0,
            "independent": totals.get("independent") or 0,
            "products_with_evidence": totals.get("products") or 0,
            "strong_products": sum(1 for row in rows if row["evidence_level"] == "strong"),
            "calibration_ready_products": sum(1 for row in rows if row["usable_for_calibration"]),
        },
    }
