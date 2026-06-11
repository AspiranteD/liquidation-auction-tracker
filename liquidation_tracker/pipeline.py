"""End-to-end orchestration: scrape -> evaluate -> store -> alert."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from . import alerts
from .calculator import BidCalculator
from .client import BStockClient, CloudflareChallenge
from .config import Settings
from .models import Auction
from .notifier import EmailNotifier, WhatsAppNotifier
from .storage import Storage

logger = logging.getLogger(__name__)


class MonitorPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = BStockClient()
        self.storage = Storage(settings.db_path)
        self.calculator = BidCalculator()
        self.notifiers = [
            EmailNotifier(settings.email),
            WhatsAppNotifier(settings.whatsapp),
        ]
        os.makedirs(settings.manifest_dir, exist_ok=True)

    def run(self, fetch_lot_ids: bool = False) -> List[Auction]:
        """Scrape every monitored country, persist results and fire reminders.

        Alerts are reminders tied to the close time, evaluated with the bid as
        it stands at that moment:

        - "t30": first run inside the 30-minute window before close, if the
          auction still qualifies.
        - "t5": last call inside the 5-minute window, only when the lot is
          still a very good deal (total cost <= the very-good ceiling).

        ``fetch_lot_ids`` opens each detail page to resolve the manifest SKU.
        It is off by default to keep the run light and avoid extra requests.
        """
        all_auctions: List[Auction] = []
        now = datetime.now(timezone.utc)
        rules = self.settings.rules

        for country in self.settings.countries:
            try:
                auctions = self.client.list_auctions(country=country)
            except CloudflareChallenge as exc:
                logger.warning("Skipping %s: %s", country, exc)
                continue

            for auction in auctions:
                if fetch_lot_ids and not auction.lot_id:
                    try:
                        self.client.fetch_lot_id(auction)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Could not fetch lot_id for %s: %s",
                            auction.auction_id,
                            exc,
                        )

                decision = alerts.evaluate(auction, rules, self.calculator)
                self.storage.upsert_auction(auction, decision.breakdown)

                if decision.is_key and auction.end_time is not None:
                    minutes_left = (
                        auction.end_time - now
                    ).total_seconds() / 60.0

                    if (
                        0 < minutes_left <= rules.final_reminder_window_min
                        and decision.very_good
                        and not self.storage.was_alerted(auction.auction_id, "t5")
                    ):
                        if self._send_alert(auction, decision, "t5", minutes_left):
                            self.storage.mark_alerted(auction.auction_id, "t5")
                            # The last call supersedes a pending first reminder.
                            self.storage.mark_alerted(auction.auction_id, "t30")
                    elif (
                        0 < minutes_left <= rules.reminder_window_min
                        and not self.storage.was_alerted(auction.auction_id, "t30")
                    ):
                        if self._send_alert(auction, decision, "t30", minutes_left):
                            self.storage.mark_alerted(auction.auction_id, "t30")

                all_auctions.append(auction)

        logger.info(
            "Run complete: %d auctions processed, %d total in DB",
            len(all_auctions),
            self.storage.count(),
        )
        return all_auctions

    def _send_alert(
        self,
        auction: Auction,
        decision: "alerts.AlertDecision",
        stage: str,
        minutes_left: Optional[float],
    ) -> bool:
        sent = any(
            [
                notifier.send_auction_alert(
                    auction, decision, stage=stage, minutes_left=minutes_left
                )
                for notifier in self.notifiers
            ]
        )
        logger.info(
            "KEY auction %s (%s) stage=%s, %.0f min left - alert sent: %s",
            auction.auction_id,
            auction.title[:50],
            stage,
            minutes_left if minutes_left is not None else -1,
            sent,
        )
        return sent
