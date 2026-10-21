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
        return bool(self.ebay_client_id and self.ebay_client_secret)

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
    "gpu": ("graphics card", "gaming gpu", "rtx gpu", "radeon gpu"),
    "cpu": ("computer processor", "amd ryzen cpu", "intel core cpu"),
    "pc": ("gaming pc", "desktop computer"),
    "laptop": ("gaming laptop", "laptop"),
    "console": ("games console", "playstation 5", "xbox series x", "steam deck", "nintendo switch"),
    "ram": ("ddr5 ram", "ddr4 ram"),
    "ssd": ("nvme ssd", "solid state drive"),
    "other": ("gaming monitor", "macbook", "ipad", "iphone"),
}

DEFAULT_SETTINGS: dict[str, str] = {
    "min_profit": "20",
    "min_roi": "0.12",
    "min_confidence": "0.42",
    "max_age_hours": "0",
    "ebay_fvf": "0.1322",
    "ebay_fixed_fee": "0.30",
    "resale_shipping": "4.50",
    "packaging_cost": "1.00",
    "collection_cost": "0.00",
    "vinted_buyer_fee_pct": "0.05",
    "vinted_buyer_fee_fixed": "0.70",
    "vinted_shipping": "3.49",
    "asking_to_sold_factor": "0.90",
    "cex_private_factor": "0.80",
    "notifications_enabled": "false",
    "discord_webhook_url": "",
    "scan_interval_vinted": "240",
    "scan_interval_gumtree": "300",
    "scan_interval_ebay": "300",
    "queries_per_cycle": "12",
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
    "other": "Other tech",
}

MARKETPLACE_LABELS: dict[str, str] = {
    "vinted": "Vinted",
    "gumtree": "Gumtree",
    "ebay": "eBay UK",
    "facebook": "Facebook",
    "cex": "CeX",
}
