"""Application configuration.

Environment variables provide credentials and infrastructure settings.
User-tunable deal thresholds and fee assumptions live in the database so they
can be edited from the dashboard without restarting the app.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_port() -> int:
    """Prefer RIGRADAR_PORT. Generic PORT is often set to 0 by dev hosts,
    which would hand the app an ephemeral port the browser cannot find."""
    for name in ("RIGRADAR_PORT", "PORT"):
        raw = os.getenv(name)
        if raw is None:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if 1 <= value <= 65535:
            return value
    return 8765


@dataclass(frozen=True)
class Settings:
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    db_path: Path = Path(os.getenv("RIGRADAR_DB", str(BASE_DIR / "data" / "rigradar.db")))
    frontend_dist: Path = BASE_DIR / "frontend" / "dist"

    host: str = os.getenv("RIGRADAR_HOST") or os.getenv("HOST") or "127.0.0.1"
    port: int = env_port()

    scanner_autostart: bool = env_bool("SCANNER_AUTOSTART", True)
    respect_robots: bool = env_bool("RESPECT_ROBOTS", True)
    user_agent: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 FlipIntel/1.0",
    )
    request_timeout: float = 25.0
    max_retries: int = 2

    ebay_client_id: str = os.getenv("EBAY_CLIENT_ID", "").strip()
    ebay_client_secret: str = os.getenv("EBAY_CLIENT_SECRET", "").strip()
    ebay_env: str = os.getenv("EBAY_ENV", "production").strip().lower()

    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    cex_enabled: bool = env_bool("CEX_ENABLED", True)

    # Archil disk: optional durable storage for files that must outlive a run.
    # The key is server-side only and must never reach the dashboard bundle.
    archil_api_key: str = os.getenv("ARCHIL_API_KEY", "").strip()
    archil_region: str = os.getenv("ARCHIL_REGION", "").strip() or "aws-us-east-1"
    archil_disk_id: str = os.getenv("ARCHIL_DISK_ID", "").strip()

    @property
    def ebay_configured(self) -> bool:
        # Read through the credential store so keys saved from Settings take
        # effect immediately, without requiring a process restart.
        from .credentials import credential_store

        return credential_store.ebay_configured

    @property
    def archil_configured(self) -> bool:
        return bool(self.archil_api_key and self.archil_disk_id)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()


# Broad discovery queries. Users never configure product searches; the scanner
# rotates these and then enriches popular models automatically from the
# catalogue. Keep this list category-level rather than specific listings.
DISCOVERY_QUERIES: dict[str, tuple[str, ...]] = {
    "gpu": (
        "graphics card", "gaming gpu", "rtx gpu", "radeon gpu", "rtx 5070",
        "rx 9070", "rtx 4070", "rx 7800 xt", "intel arc gpu",
    ),
    "cpu": (
        "computer processor", "amd ryzen cpu", "intel core cpu", "ryzen 7",
        "core i7", "ryzen 5", "core i5", "ryzen 9",
    ),
    "pc": ("gaming pc", "desktop computer", "gaming desktop", "mini pc", "workstation pc"),
    "laptop": (
        "gaming laptop", "laptop", "macbook pro", "macbook air", "thinkpad",
        "windows laptop", "student laptop",
    ),
    "console": (
        "games console", "playstation 5", "xbox series x", "nintendo switch",
        "playstation 4", "xbox one", "nintendo switch lite", "xbox series s",
    ),
    "handheld": ("steam deck", "rog ally", "legion go", "playstation portal", "handheld gaming"),
    "retro": (
        "retro console", "game boy", "nintendo 64", "sega mega drive",
        "super nintendo", "gamecube", "ps vita",
    ),
    "ram": ("ddr5 ram", "ddr4 ram", "ddr5 32gb", "ddr4 16gb", "ddr5 64gb", "laptop ram"),
    "ssd": (
        "nvme ssd", "solid state drive", "sata ssd", "external ssd", "hard drive",
        "m.2 ssd", "portable ssd",
    ),
    "motherboard": ("motherboard", "b650 motherboard", "am5 motherboard", "z790 motherboard"),
    "monitor": ("gaming monitor", "4k monitor", "1440p monitor", "ultrawide monitor", "oled monitor"),
    "phone": ("iphone", "samsung galaxy", "google pixel", "smartphone"),
    "tablet": ("ipad", "ipad pro", "samsung tablet", "lenovo tablet"),
    "networking": (
        "wifi router", "mesh wifi", "network switch", "unifi", "netgear router",
        "tp-link router", "access point",
    ),
    "other": ("macbook", "gaming headset", "capture card"),
}

DEFAULT_SETTINGS: dict[str, str] = {
    "min_profit": "20",
    "min_roi": "0.12",
    "min_confidence": "0.42",
    "max_age_hours": "0",
    "ebay_fvf": "0.1322",
    "ebay_fixed_fee": "0.30",
    "resale_channel": "ebay",
    "resale_shipping": "4.50",
    "packaging_cost": "1.00",
    "collection_cost": "0.00",
    "other_costs": "0.00",
    "vinted_buyer_fee_pct": "0.05",
    "vinted_buyer_fee_fixed": "0.70",
    "vinted_shipping": "3.49",
    "asking_to_sold_factor": "0.85",
    "cex_private_factor": "0.80",
    "cex_refresh_limit": "6",
    "cex_reuse_hours": "168",
    "history_reuse_days": "7",
    "resale_strategy": "balanced",
    "auction_scan_seconds": "300",
    "auction_urgent_seconds": "300",
    "auction_window_minutes": "30",
    "auction_alert_window_minutes": "15",
    "auction_alerts_enabled": "true",
    "notification_cooldown_minutes": "180",
    "notifications_enabled": "false",
    "discord_webhook_url": "",
    "scan_interval_vinted": "1800",
    "scan_interval_gumtree": "1800",
    "scan_interval_ebay": "1800",
    "queries_per_cycle": "12",
    "discovery_mode": "category",
    "category_cycle_seconds": "1800",
    "category_requests_per_cycle": "12",
    "ebay_category_requests_per_cycle": "4",
    "category_backfill_pages": "10",
    "category_incremental_pages": "2",
    "category_overlap_stop": "0.75",
    "keyword_supplement_queries": "2",
    "enabled_vinted": "true",
    "enabled_gumtree": "true",
    "enabled_ebay": "true",
    "enabled_facebook": "false",
    "last_query_cursor": "0",
}

CATEGORY_LABELS: dict[str, str] = {
    "gpu": "Graphics cards",
    "cpu": "Processors",
    "ram": "Memory",
    "ssd": "Storage",
    "motherboard": "Motherboards",
    "pc": "Complete PCs",
    "laptop": "Laptops",
    "console": "Consoles",
    "monitor": "Monitors",
    "phone": "Phones",
    "tablet": "Tablets",
    "networking": "Networking",
    "other": "Other tech",
}

MARKETPLACE_LABELS: dict[str, str] = {
    "vinted": "Vinted",
    "gumtree": "Gumtree",
    "ebay": "eBay UK",
    "facebook": "Facebook",
    "cex": "CeX",
}
