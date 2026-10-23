"""SQLite persistence. One connection guarded by a lock is plenty for a
single-user local app and keeps WAL writes deterministic."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import DEFAULT_SETTINGS, settings as app_settings
from .utils import iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marketplace TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    image_url TEXT,
    price REAL NOT NULL,
    shipping REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'GBP',
    condition_raw TEXT,
    condition_norm TEXT,
    location TEXT,
    seller TEXT,
    listing_kind TEXT,
    category TEXT,
    product_key TEXT,
    product_name TEXT,
    identification_confidence REAL NOT NULL DEFAULT 0,
    is_faulty INTEGER NOT NULL DEFAULT 0,
    is_wanted INTEGER NOT NULL DEFAULT 0,
    is_bundle INTEGER NOT NULL DEFAULT 0,
    duplicate_of INTEGER,
    duplicate_score REAL,
    flags TEXT,
    attrs TEXT,
    is_auction INTEGER NOT NULL DEFAULT 0,
    posted_at TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_price REAL,
    previous_price REAL,
    price_dropped_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    search_query TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (marketplace, external_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_key ON listings(product_key, listing_kind, status);
CREATE INDEX IF NOT EXISTS idx_listings_seen ON listings(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price);
CREATE INDEX IF NOT EXISTS idx_listings_kind ON listings(category, status);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price REAL NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id, observed_at);

CREATE TABLE IF NOT EXISTS market_comps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_key TEXT NOT NULL,
    marketplace TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    price REAL NOT NULL,
    kind TEXT,
    condition_norm TEXT,
    is_sold INTEGER NOT NULL DEFAULT 0,
    postage REAL NOT NULL DEFAULT 0,
    notes TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    evidence_confidence REAL NOT NULL DEFAULT 1.0,
    url TEXT,
    observed_at TEXT NOT NULL,
    created_at TEXT,
    currency TEXT NOT NULL DEFAULT 'GBP',
    product_name TEXT,
    condition_raw TEXT,
    variants TEXT,
    import_id INTEGER,
    updated_at TEXT,
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_comps_key ON market_comps(product_key, kind);
CREATE INDEX IF NOT EXISTS idx_comps_sold ON market_comps(product_key, is_sold, observed_at DESC);

CREATE TABLE IF NOT EXISTS market_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_key TEXT NOT NULL,
    kind TEXT,
    marketplace TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    price REAL NOT NULL,
    condition_norm TEXT,
    observed_at TEXT NOT NULL,
    observed_day TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (marketplace, source_id, observed_day)
);
CREATE INDEX IF NOT EXISTS idx_market_obs_key ON market_observations(product_key, observed_at DESC);

CREATE TABLE IF NOT EXISTS api_usage_daily (
    marketplace TEXT NOT NULL,
    day TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    last_status INTEGER,
    last_error TEXT,
    last_request_at TEXT,
    last_success_at TEXT,
    quota_remaining INTEGER,
    quota_limit INTEGER,
    quota_reset_at TEXT,
    PRIMARY KEY (marketplace, day)
);
CREATE INDEX IF NOT EXISTS idx_api_usage_day ON api_usage_daily(day DESC);

CREATE TABLE IF NOT EXISTS evidence_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    created_at TEXT NOT NULL,
    file_sha256 TEXT,
    total_rows INTEGER NOT NULL DEFAULT 0,
    imported INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS external_quotes (
    product_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT,
    sell_price REAL,
    cash_price REAL,
    url TEXT,
    status TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    listing_id INTEGER PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    product_key TEXT,
    kind TEXT,
    expected_resale REAL,
    resale_low REAL,
    resale_high REAL,
    confidence REAL NOT NULL DEFAULT 0,
    sample_size INTEGER NOT NULL DEFAULT 0,
    valuation_method TEXT,
    purchase_total REAL NOT NULL DEFAULT 0,
    resale_fees REAL NOT NULL DEFAULT 0,
    resale_shipping REAL NOT NULL DEFAULT 0,
    packaging REAL NOT NULL DEFAULT 0,
    net_profit REAL NOT NULL DEFAULT 0,
    net_profit_conservative REAL,
    net_profit_optimistic REAL,
    roi REAL NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,
    is_deal INTEGER NOT NULL DEFAULT 0,
    needs_review INTEGER NOT NULL DEFAULT 0,
    review_reason TEXT,
    reasons TEXT,
    breakdown TEXT,
    computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deals_feed ON deals(is_deal, score DESC);
CREATE INDEX IF NOT EXISTS idx_deals_profit ON deals(net_profit DESC);

CREATE TABLE IF NOT EXISTS saved_deals (
    listing_id INTEGER PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dismissed_deals (
    listing_id INTEGER PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marketplace TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    queries TEXT,
    items_found INTEGER NOT NULL DEFAULT 0,
    items_new INTEGER NOT NULL DEFAULT 0,
    price_drops INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    error TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_scan_runs_market ON scan_runs(marketplace, started_at DESC);

CREATE TABLE IF NOT EXISTS marketplace_status (
    marketplace TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'idle',
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_scan_at TEXT,
    listings_seen INTEGER NOT NULL DEFAULT 0,
    last_duration_ms INTEGER,
    checked_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER REFERENCES listings(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    ok INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (listing_id, channel)
);

CREATE TABLE IF NOT EXISTS review_decisions (
    listing_id INTEGER PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    note TEXT,
    product_key TEXT,
    kind TEXT,
    decided_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_decisions ON review_decisions(decision, decided_at DESC);

CREATE TABLE IF NOT EXISTS comp_cache (
    product_key TEXT NOT NULL,
    marketplace TEXT NOT NULL,
    last_fetched_at TEXT NOT NULL,
    status TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (product_key, marketplace)
);
"""


class Database:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or app_settings.db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA busy_timeout=30000")
            self.conn.executescript(SCHEMA)
            self._migrate()
            self._seed_settings()
            self._seed_marketplaces()
            self.conn.commit()

    def _migrate(self) -> None:
        """Additive migrations only. Never drops user listings or evidence."""
        migrations: dict[str, dict[str, str]] = {
            "listings": {
                "duplicate_of": "INTEGER",
                "duplicate_score": "REAL",
            },
            "market_comps": {
                "postage": "REAL NOT NULL DEFAULT 0",
                "notes": "TEXT",
                "verified": "INTEGER NOT NULL DEFAULT 0",
                "evidence_confidence": "REAL NOT NULL DEFAULT 1.0",
                "created_at": "TEXT",
                "currency": "TEXT NOT NULL DEFAULT 'GBP'",
                "product_name": "TEXT",
                "condition_raw": "TEXT",
                "variants": "TEXT",
                "import_id": "INTEGER",
                "updated_at": "TEXT",
            },
            "deals": {
                "needs_review": "INTEGER NOT NULL DEFAULT 0",
                "review_reason": "TEXT",
                "net_profit_conservative": "REAL",
                "net_profit_optimistic": "REAL",
                "risk_score": "REAL NOT NULL DEFAULT 0",
                "risk_level": "TEXT",
                "risk_reasons": "TEXT",
            },
            "scan_runs": {
                "duplicates": "INTEGER NOT NULL DEFAULT 0",
            },
            "api_usage_daily": {
                "last_success_at": "TEXT",
            },
        }
        for table, columns in migrations.items():
            existing = {
                row[1] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_listings_duplicate ON listings(duplicate_of)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_deals_review ON deals(needs_review, computed_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comps_import ON market_comps(import_id)"
        )

    def _seed_settings(self) -> None:
        now = iso()
        self.conn.executemany(
            "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
            [(k, v, now) for k, v in DEFAULT_SETTINGS.items()],
        )

    def _seed_marketplaces(self) -> None:
        for name in ("vinted", "gumtree", "ebay", "facebook"):
            self.conn.execute(
                "INSERT OR IGNORE INTO marketplace_status(marketplace, state) VALUES (?, 'idle')",
                (name,),
            )

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    # --- settings -----------------------------------------------------------
    def get_settings(self) -> dict[str, str]:
        rows = self.query("SELECT key, value FROM settings")
        values = dict(DEFAULT_SETTINGS)
        values.update({row["key"]: row["value"] for row in rows})
        return values

    def set_settings(self, values: dict[str, Any]) -> None:
        now = iso()
        with self._lock:
            self.conn.executemany(
                "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                [(str(k), str(v), now) for k, v in values.items()],
            )
            self.conn.commit()

    def setting_float(self, key: str, default: float = 0.0) -> float:
        raw = self.get_settings().get(key)
        try:
            return float(raw) if raw is not None else default
        except (TypeError, ValueError):
            return default

    def setting_int(self, key: str, default: int = 0) -> int:
        raw = self.get_settings().get(key)
        try:
            return int(float(raw)) if raw is not None else default
        except (TypeError, ValueError):
            return default

    def setting_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get_settings().get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def close(self) -> None:
        with self._lock:
            self.conn.close()
