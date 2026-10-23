"""Optional Discord notifications for deals, offers and ending auctions.

Alerts are deliberately typed: a newly discovered Buy It Now bargain, a Best
Offer opportunity, an auction ending soon and a material change to an auction
already alerted are different events with different fields. Every alert is
deduplicated, expired auctions are never sent and no alert claims a profit that
the valuation did not support.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import httpx

from .db import Database
from .utils import hours_ago, iso, parse_iso, utcnow

log = logging.getLogger(__name__)

COLOUR_DEAL = 0x35D07F
COLOUR_OFFER = 0x4AA8FF
COLOUR_AUCTION = 0xF0B429
COLOUR_UPDATE = 0xE06C75


def _enabled(values: dict[str, Any]) -> bool:
    return str(values.get("notifications_enabled", "false")).lower() in {
        "1", "true", "yes", "on",
    }


def _evidence_label(level: str | None, confidence: float | None) -> str:
    name = (level or "insufficient").title()
    return f"{name} ({confidence * 100:.0f}%)" if confidence is not None else name


class Notifier:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self.client.aclose()

    async def _webhook(self) -> str | None:
        values = self.db.get_settings()
        if not _enabled(values):
            return None
        webhook = (values.get("discord_webhook_url") or "").strip()
        if not webhook.startswith("https://discord.com/api/webhooks/") and not webhook.startswith(
            "https://discordapp.com/api/webhooks/"
        ):
            return None
        return webhook

    async def _send(
        self,
        webhook: str,
        *,
        content: str,
        embed: dict[str, Any],
        listing_id: int | None = None,
        channel: str = "discord",
        record_notification: bool = True,
    ) -> bool:
        try:
            response = await self.client.post(
                webhook,
                json={
                    "username": "FlipIntel",
                    "content": content,
                    "embeds": [embed],
                    "allowed_mentions": {"parse": []},
                },
            )
            ok = 200 <= response.status_code < 300
            error = None if ok else f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            ok = False
            error = f"{type(exc).__name__}"
        if record_notification and listing_id is not None:
            self.db.execute(
                "INSERT OR IGNORE INTO notifications(listing_id, channel, ok, error, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (listing_id, channel, int(ok), error, iso()),
            )
        if not ok:
            log.warning("Discord notification failed: %s", error)
        return ok

    async def notify_new_deals(self, limit: int = 5) -> int:
        webhook = await self._webhook()
        if not webhook:
            return 0
        rows = self.db.query(
            """
            SELECT l.*, d.expected_resale, d.net_profit, d.roi, d.confidence, d.score,
                   d.reasons, d.evidence_level, d.max_bid, d.resale_strategy,
                   d.estimated_days, d.risk_level
            FROM deals d
            JOIN listings l ON l.id = d.listing_id
            LEFT JOIN dismissed_deals x ON x.listing_id = l.id
            LEFT JOIN notifications n ON n.listing_id = l.id AND n.channel = 'discord'
            WHERE d.is_deal = 1 AND l.status = 'active' AND x.listing_id IS NULL
              AND n.id IS NULL AND l.first_seen >= ? AND l.is_auction = 0
            ORDER BY d.score DESC
            LIMIT ?
            """,
            (hours_ago(48), limit),
        )
        sent = 0
        for row in rows:
            offer = self._best_offer_available(row)
            fields = [
                {"name": "Total cost", "value": f"£{row['price']:,.2f}", "inline": True},
                {"name": "Expected resale", "value": f"£{row['expected_resale']:,.2f}", "inline": True},
                {"name": "Net profit", "value": f"£{row['net_profit']:,.0f}", "inline": True},
                {"name": "ROI", "value": f"{(row['roi'] or 0) * 100:.0f}%", "inline": True},
                {
                    "name": "Evidence",
                    "value": _evidence_label(row["evidence_level"], row["confidence"]),
                    "inline": True,
                },
                {"name": "Source", "value": str(row["marketplace"]).title(), "inline": True},
            ]
            if offer and row["max_bid"]:
                fields.append(
                    {
                        "name": "Maximum offer",
                        "value": f"£{row['max_bid']:,.2f} (still profitable after fees)",
                        "inline": False,
                    }
                )
            if row["risk_level"] and row["risk_level"] != "low":
                fields.append(
                    {
                        "name": "Risk",
                        "value": f"{str(row['risk_level']).title()} — check the signals before buying",
                        "inline": False,
                    }
                )
            embed = {
                "title": (row["title"] or "")[:250],
                "url": row["url"],
                "color": COLOUR_OFFER if offer else COLOUR_DEAL,
                "fields": fields,
                "footer": {
                    "text": "FlipIntel · asking-price evidence unless completed sales are shown"
                },
            }
            if row.get("image_url"):
                embed["thumbnail"] = {"url": row["image_url"]}
            if await self._send(
                webhook,
                content=(
                    "Best Offer opportunity detected" if offer else "New UK hardware deal detected"
                ),
                embed=embed,
                listing_id=int(row["id"]),
            ):
                sent += 1
            else:
                break
        return sent

    def _best_offer_available(self, row: dict[str, Any]) -> bool:
        try:
            import json

            raw = json.loads(row.get("raw_json") or "{}")
        except (TypeError, ValueError):
            return False
        options = raw.get("buying_options") or []
        return "BEST_OFFER" in options

    async def notify_auctions(self, limit: int = 4) -> int:
        """Alert on profitable auctions inside the notification window."""
        webhook = await self._webhook()
        if not webhook:
            return 0
        values = self.db.get_settings()
        try:
            window = int(float(values.get("auction_window_minutes", 30)))
        except (TypeError, ValueError):
            window = 30
        try:
            cooldown = int(float(values.get("notification_cooldown_minutes", 180)))
        except (TypeError, ValueError):
            cooldown = 180
        cutoff = iso(utcnow() + timedelta(minutes=max(5, window)))
        rows = self.db.query(
            """
            SELECT l.*, d.expected_resale, d.net_profit, d.roi, d.confidence,
                   d.evidence_level, d.max_bid, d.risk_level
            FROM listings l JOIN deals d ON d.listing_id = l.id
            LEFT JOIN dismissed_deals x ON x.listing_id = l.id
            WHERE l.status='active' AND l.is_auction=1 AND x.listing_id IS NULL
              AND l.auction_end_at IS NOT NULL AND l.auction_end_at >= ?
              AND l.auction_end_at <= ?
              AND d.max_bid IS NOT NULL AND l.price <= d.max_bid
            ORDER BY l.auction_end_at ASC
            LIMIT 40
            """,
            (iso(), cutoff),
        )
        sent = 0
        for row in rows:
            listing_id = int(row["id"])
            last = self.db.query_one(
                "SELECT bid, sent_at FROM auction_alerts WHERE listing_id=? "
                "ORDER BY id DESC LIMIT 1",
                (listing_id,),
            )
            current = float(row["current_bid"] or row["price"] or 0)
            previous = float((last or {}).get("bid") or 0)
            changed = bool(last) and current > previous * 1.05
            fresh = not last
            if last:
                last_at = parse_iso(last.get("sent_at"))
                if last_at and (utcnow() - last_at).total_seconds() < cooldown * 60 and not changed:
                    continue
            if not fresh and not changed:
                continue
            end = parse_iso(row.get("auction_end_at"))
            ends_in = int((end - utcnow()).total_seconds() / 60) if end else None
            if ends_in is not None and ends_in <= 0:
                continue
            embed = {
                "title": (row["title"] or "")[:250],
                "url": row["url"],
                "color": COLOUR_UPDATE if last else COLOUR_AUCTION,
                "fields": [
                    {"name": "Current bid", "value": f"£{current:,.2f}", "inline": True},
                    {"name": "Bids", "value": str(row["bid_count"] or "—"), "inline": True},
                    {
                        "name": "Ends",
                        "value": f"in {ends_in} min" if ends_in is not None else "unknown",
                        "inline": True,
                    },
                    {
                        "name": "Maximum bid",
                        "value": f"£{row['max_bid']:,.2f}",
                        "inline": True,
                    },
                    {"name": "Expected resale", "value": f"£{row['expected_resale']:,.2f}", "inline": True},
                    {"name": "Net profit at ceiling", "value": f"£{row['net_profit']:,.0f}", "inline": True},
                    {
                        "name": "Evidence",
                        "value": _evidence_label(row["evidence_level"], row["confidence"]),
                        "inline": True,
                    },
                ],
                "footer": {"text": "FlipIntel · place the bid yourself; this is not an automatic bid"},
            }
            if row.get("image_url"):
                embed["thumbnail"] = {"url": row["image_url"]}
            ok = await self._send(
                webhook,
                content=(
                    "Auction bid increased — still under the profitable ceiling"
                    if last else "Profitable auction ending soon"
                ),
                embed=embed,
                listing_id=None,
                record_notification=False,
            )
            if not ok:
                break
            self.db.execute(
                "INSERT INTO auction_alerts(listing_id, channel, bid, profitable, sent_at) "
                "VALUES (?, 'discord', ?, 1, ?)",
                (listing_id, current, iso()),
            )
            sent += 1
            if sent >= limit:
                break
        return sent
