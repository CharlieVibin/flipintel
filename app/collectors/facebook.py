"""Facebook Marketplace.

Facebook requires an authenticated session to view Marketplace results and its
terms do not permit automated collection. Rather than shipping a broken scraper
that silently returns nothing, this source explicitly reports that it is
unavailable and is excluded from scans. It can be replaced by a compliant
connector later without touching the rest of the pipeline.
"""
from __future__ import annotations

from .base import BaseCollector, SearchOutcome


class FacebookCollector(BaseCollector):
    marketplace = "facebook"
    label = "Facebook Marketplace"
    base_url = "https://www.facebook.com/marketplace"

    @property
    def available(self) -> tuple[bool, str | None]:
        return False, (
            "Facebook Marketplace requires a signed-in session and does not "
            "permit automated collection. This source is intentionally disabled; "
            "listings are not fabricated."
        )

    async def search(self, queries: list[str], max_pages: int = 1) -> SearchOutcome:
        available, reason = self.available
        return SearchOutcome(
            marketplace=self.marketplace,
            status="unavailable",
            error=reason,
        )
