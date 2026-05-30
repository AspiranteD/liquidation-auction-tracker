"""End-to-end orchestration: scrape -> evaluate -> store -> alert."""
from __future__ import annotations

import logging
import os
from typing import List

from . import alerts
from .calculator import BidCalculator
from .client import BStockClient, CloudflareChallenge
from .config import Settings
from .models import Auction
from .notifier import EmailNotifier
from .storage import Storage

logger = logging.getLogger(__name__)


class MonitorPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = BStockClient()
        self.storage = Storage(settings.db_path)
        self.calculator = BidCalculator()
        self.notifier = EmailNotifier(settings.email)
        os.makedirs(settings.manifest_dir, exist_ok=True)

    def run(self, fetch_lot_ids: bool = False) -> List[Auction]:
        """Scrape every monitored country, persist results and fire alerts.

        ``fetch_lot_ids`` opens each detail page to resolve the manifest SKU.
        It is off by default to keep the run light and avoid extra requests.
        """
        all_auctions: List[Auction] = []

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

                decision = alerts.evaluate(
                    auction, self.settings.rules, self.calculator
                )
                self.storage.upsert_auction(auction, decision.breakdown)

                if decision.is_key and not self.storage.was_alerted(auction.auction_id):
                    sent = self.notifier.send_auction_alert(auction, decision)
                    if sent:
                        self.storage.mark_alerted(auction.auction_id)
                    logger.info(
                        "KEY auction %s (%s) - alert sent: %s",
                        auction.auction_id,
                        auction.title[:50],
                        sent,
                    )

                all_auctions.append(auction)

        logger.info(
            "Run complete: %d auctions processed, %d total in DB",
            len(all_auctions),
            self.storage.count(),
        )
        return all_auctions
