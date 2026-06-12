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
from .notifier import CallNotifier, EmailNotifier, WhatsAppNotifier
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
        self.caller = CallNotifier(settings.call)
        os.makedirs(settings.manifest_dir, exist_ok=True)

    def run(self, fetch_lot_ids: bool = False) -> List[Auction]:
        """Scrape every monitored country, persist results and fire reminders.

        Alerts are a reminder ladder tied to the close time, evaluated with
        the bid as it stands at each run (the monitor runs every minute):

        - One WhatsApp/email per stage as the close approaches (default
          stages: 30, 15, 10 and 5 minutes), while the lot still qualifies.
        - At ``call_at_minutes`` (default 5) or less, an additional voice
          call (CallMeBot Telegram call), once per auction.

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

                stages = sorted(rules.reminder_stages, reverse=True)
                if (
                    decision.is_key
                    and auction.end_time is not None
                    and stages
                ):
                    minutes_left = (
                        auction.end_time - now
                    ).total_seconds() / 60.0

                    if 0 < minutes_left <= max(stages):
                        # Tightest stage containing the current time-to-close
                        # (e.g. 12 min left -> the 15-minute stage).
                        current = min(s for s in stages if minutes_left <= s)
                        stage_name = f"t{current}"
                        if not self.storage.was_alerted(
                            auction.auction_id, stage_name
                        ):
                            if self._send_alert(
                                auction, decision, stage_name, minutes_left
                            ):
                                # Skipped wider stages can never fire later;
                                # mark them so the ladder state stays clean.
                                for s in stages:
                                    if s >= current:
                                        self.storage.mark_alerted(
                                            auction.auction_id, f"t{s}"
                                        )

                        if (
                            minutes_left <= rules.call_at_minutes
                            and not self.storage.was_alerted(
                                auction.auction_id, "call"
                            )
                        ):
                            called = self.caller.call_auction_alert(
                                auction, decision, minutes_left
                            )
                            if called:
                                self.storage.mark_alerted(auction.auction_id, "call")
                            logger.info(
                                "KEY auction %s call escalation - placed: %s",
                                auction.auction_id,
                                called,
                            )

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
