"""Optional Discord notifications for new qualifying deals."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .db import Database
from .utils import hours_ago, iso

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self.client.aclose()

    async def notify_new_deals(self, limit: int = 5) -> int:
        values = self.db.get_settings()
        if str(values.get("notifications_enabled", "false")).lower() not in {"1", "true", "yes", "on"}:
            return 0
        webhook = (values.get("discord_webhook_url") or "").strip()
        if not webhook.startswith("https://discord.com/api/webhooks/") and not webhook.startswith(
            "https://discordapp.com/api/webhooks/"
        ):
            return 0
        rows = self.db.query(
            """
            SELECT l.*, d.expected_resale, d.net_profit, d.roi, d.confidence, d.score, d.reasons
            FROM deals d
            JOIN listings l ON l.id = d.listing_id
            LEFT JOIN dismissed_deals x ON x.listing_id = l.id
            LEFT JOIN notifications n ON n.listing_id = l.id AND n.channel = 'discord'
            WHERE d.is_deal = 1 AND l.status = 'active' AND x.listing_id IS NULL
              AND n.id IS NULL AND l.first_seen >= ?
            ORDER BY d.score DESC
            LIMIT ?
            """,
            (hours_ago(48), limit),
        )
        sent = 0
        for row in rows:
            embed = {
                "title": (row["title"] or "")[:250],
                "url": row["url"],
                "color": 0x35D07F,
                "fields": [
                    {"name": "Asking", "value": f"£{row['price']:,.2f}", "inline": True},
                    {"name": "Resale est.", "value": f"£{row['expected_resale']:,.2f}", "inline": True},
                    {"name": "Net profit", "value": f"£{row['net_profit']:,.0f}", "inline": True},
                    {"name": "ROI", "value": f"{(row['roi'] or 0) * 100:.0f}%", "inline": True},
                    {"name": "Confidence", "value": f"{(row['confidence'] or 0) * 100:.0f}%", "inline": True},
                    {"name": "Source", "value": row["marketplace"].title(), "inline": True},
                ],
                "footer": {"text": "FlipIntel · verify condition and model before buying"},
            }
            if row.get("image_url"):
                embed["thumbnail"] = {"url": row["image_url"]}
            try:
                response = await self.client.post(
                    webhook,
                    json={
                        "username": "FlipIntel",
                        "content": "New UK hardware deal detected",
                        "embeds": [embed],
                        "allowed_mentions": {"parse": []},
                    },
                )
                ok = 200 <= response.status_code < 300
                error = None if ok else f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                ok = False
                error = f"{type(exc).__name__}"
            self.db.execute(
                "INSERT OR IGNORE INTO notifications(listing_id, channel, ok, error, created_at) "
                "VALUES (?, 'discord', ?, ?, ?)",
                (row["id"], int(ok), error, iso()),
            )
            if ok:
                sent += 1
            else:
                log.warning("Discord notification failed: %s", error)
                break
        return sent
