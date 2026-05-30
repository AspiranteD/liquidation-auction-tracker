"""Email notifications for key auctions (SMTP, e.g. Gmail app password)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

from .alerts import AlertDecision
from .config import EmailConfig
from .models import Auction

logger = logging.getLogger(__name__)


def build_alert_body(auction: Auction, decision: AlertDecision) -> str:
    b = decision.breakdown
    lines = [
        f"Key liquidation auction detected: {auction.title}",
        "",
        f"Auction ID : {auction.auction_id}",
        f"Country    : {auction.country}",
        f"Lot type   : {auction.lot_type}",
        f"Retail     : EUR {auction.retail_value:,.2f}" if auction.retail_value else "Retail     : n/a",
        f"Pieces     : {auction.pieces}",
        f"Current bid: EUR {auction.current_bid:,.2f}" if auction.current_bid else "Current bid: n/a",
        f"Ends       : {auction.end_time}",
        f"URL        : {auction.url}",
    ]
    if b:
        lines += [
            "",
            "Suggested max bid (to stay within target landed cost):",
            f"  Max bid       : EUR {b.bid:,.2f}",
            f"  Transport     : EUR {b.transport:,.2f}",
            f"  VAT (21%)     : EUR {b.vat:,.2f}",
            f"  B-Stock fee   : EUR {b.bstock_fee:,.2f}",
            f"  RE (5.2%)     : EUR {b.re:,.2f}",
            f"  Total landed  : EUR {b.total_cost:,.2f}",
        ]
        if b.total_pct_of_retail is not None:
            lines.append(f"  % of retail   : {b.total_pct_of_retail:.1%}")
    return "\n".join(lines)


class EmailNotifier:
    def __init__(self, config: EmailConfig) -> None:
        self.config = config

    def send(self, subject: str, body: str) -> bool:
        cfg = self.config
        if not cfg.enabled:
            logger.info("Email alerts disabled; skipping send for: %s", subject)
            return False
        if not (cfg.username and cfg.password and cfg.recipients):
            logger.warning("Email config incomplete; cannot send alert.")
            return False

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg.sender or cfg.username
        msg["To"] = ", ".join(cfg.recipients)
        msg.set_content(body)

        try:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(cfg.username, cfg.password)
                server.send_message(msg)
            logger.info("Alert email sent: %s", subject)
            return True
        except Exception as exc:  # noqa: BLE001 - surface any SMTP failure
            logger.error("Failed to send alert email: %s", exc)
            return False

    def send_auction_alert(
        self, auction: Auction, decision: AlertDecision
    ) -> bool:
        subject = (
            f"[Liquidation Alert] {auction.country} {auction.lot_type} - "
            f"retail EUR {auction.retail_value:,.0f}"
            if auction.retail_value
            else f"[Liquidation Alert] {auction.title[:60]}"
        )
        return self.send(subject, build_alert_body(auction, decision))
