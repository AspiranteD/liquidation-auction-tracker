"""Alert notifications for key auctions: email (SMTP) and WhatsApp (CallMeBot)."""
from __future__ import annotations

import logging
import os
import re
import smtplib
from email.message import EmailMessage
from typing import Optional

import requests

from .alerts import AlertDecision
from .config import CallConfig, EmailConfig, WhatsAppConfig
from .models import Auction

logger = logging.getLogger(__name__)


def _stage_minutes(stage: str) -> Optional[int]:
    """'t30' -> 30, 't5' -> 5; None for non-ladder stages."""
    if stage.startswith("t") and stage[1:].isdigit():
        return int(stage[1:])
    return None


def _is_last_call(stage: str) -> bool:
    minutes = _stage_minutes(stage)
    return minutes is not None and minutes <= 5


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

    def send(
        self, subject: str, body: str, attachments: Optional[list] = None
    ) -> bool:
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

        for path in attachments or []:
            with open(path, "rb") as fh:
                msg.add_attachment(
                    fh.read(),
                    maintype="application",
                    subtype="pdf",
                    filename=os.path.basename(path),
                )

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
        self,
        auction: Auction,
        decision: AlertDecision,
        stage: str = "t30",
        minutes_left: Optional[float] = None,
    ) -> bool:
        prefix = "[ULTIMA LLAMADA]" if _is_last_call(stage) else "[Liquidation Alert]"
        subject = (
            f"{prefix} {auction.country} {auction.lot_type} - "
            f"retail EUR {auction.retail_value:,.0f}"
            if auction.retail_value
            else f"{prefix} {auction.title[:60]}"
        )
        body = build_alert_body(auction, decision)
        if minutes_left is not None:
            body = f"Closes in ~{minutes_left:.0f} minutes.\n\n{body}"
        return self.send(subject, body)


def build_whatsapp_body(
    auction: Auction,
    decision: AlertDecision,
    stage: str = "t30",
    minutes_left: Optional[float] = None,
) -> str:
    """Compact, mobile-friendly version of the alert (WhatsApp message)."""
    b = decision.breakdown
    retail = f"EUR {auction.retail_value:,.0f}" if auction.retail_value else "n/a"
    bid = f"EUR {auction.current_bid:,.0f}" if auction.current_bid else "sin puja"
    mins = f"{minutes_left:.0f}" if minutes_left is not None else "?"

    if _is_last_call(stage):
        header = f"🔥 ÚLTIMA LLAMADA: cierra en {mins} min"
    else:
        header = f"⏰ Cierra en {mins} min — B-Stock ({auction.country})"

    lines = [
        header,
        f"{auction.lot_type or 'Lote'} — retail {retail}, {auction.pieces or '?'} uds",
    ]
    if decision.current_bid_pct is not None:
        lines.append(
            f"Puja actual: {bid} → coste total {decision.current_bid_pct:.1%} del retail"
        )
    else:
        lines.append(f"Puja actual: {bid}")
    if decision.recommended_bid:
        lines.append(
            f"Puja máx recomendada (recuperación {decision.recovery:.0%}/"
            f"caja×3): EUR {decision.recommended_bid:,.0f}"
            + (f" (coste total EUR {b.total_cost:,.0f})" if b else "")
        )
    if auction.end_time:
        lines.append(f"Cierra: {auction.end_time:%d/%m %H:%M}")
    lines.append(auction.url)
    return "\n".join(lines)


class WhatsAppNotifier:
    """Sends WhatsApp messages through the free CallMeBot API.

    Requires a one-time setup: add CallMeBot's number on WhatsApp and send
    "I allow callmebot to send me messages" to receive your apikey.
    """

    API_URL = "https://api.callmebot.com/whatsapp.php"

    def __init__(self, config: WhatsAppConfig, timeout: int = 60) -> None:
        self.config = config
        self.timeout = timeout

    def send(self, text: str) -> bool:
        cfg = self.config
        if not cfg.enabled:
            logger.info("WhatsApp alerts disabled; skipping send.")
            return False
        if not (cfg.phone and cfg.apikey):
            logger.warning("WhatsApp config incomplete; cannot send alert.")
            return False

        # CallMeBot delivers the text via a GET querystring; keep it well
        # under URL-length limits.
        if len(text) > 1800:
            text = text[:1797] + "..."

        try:
            response = requests.get(
                self.API_URL,
                params={"phone": cfg.phone, "text": text, "apikey": cfg.apikey},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.error("Failed to send WhatsApp alert: %s", exc)
            return False

        # CallMeBot answers 200 even for some errors, so check the body too.
        body = response.text or ""
        if response.status_code >= 400 or "APIKey is invalid" in body:
            logger.error(
                "CallMeBot rejected the message (HTTP %s): %s",
                response.status_code,
                body[:200],
            )
            return False
        logger.info("WhatsApp alert sent to %s", cfg.phone)
        return True

    def send_auction_alert(
        self,
        auction: Auction,
        decision: AlertDecision,
        stage: str = "t30",
        minutes_left: Optional[float] = None,
    ) -> bool:
        return self.send(build_whatsapp_body(auction, decision, stage, minutes_left))


def build_call_text(
    auction: Auction, decision: AlertDecision, minutes_left: Optional[float]
) -> str:
    """Short Spanish TTS script (CallMeBot caps the text at 256 chars)."""
    mins = f"{minutes_left:.0f}" if minutes_left is not None else "pocos"
    parts = [
        f"Atención. Subasta a punto de cerrar en {mins} minutos.",
        f"{auction.lot_type or 'Lote'} con retail de "
        f"{(auction.retail_value or 0):.0f} euros.",
    ]
    if auction.current_bid:
        parts.append(f"Puja actual {auction.current_bid:.0f} euros.")
    if decision.recommended_bid:
        parts.append(
            f"Máximo recomendado {decision.recommended_bid:.0f} euros "
            f"(recuperación {decision.recovery * 100:.0f} por ciento)."
        )
    return " ".join(parts)[:256]


class CallNotifier:
    """Voice-call escalation via CallMeBot's free Telegram call API.

    Rings the user on Telegram and reads the alert with a TTS voice. Needs
    a one-time authorization: send /start to @CallMeBot_txtbot on Telegram.
    """

    API_URL = "http://api.callmebot.com/start.php"

    def __init__(self, config: CallConfig, timeout: int = 60) -> None:
        self.config = config
        self.timeout = timeout

    def call(self, text: str) -> bool:
        cfg = self.config
        if not cfg.enabled:
            logger.info("Call alerts disabled; skipping call.")
            return False
        if not cfg.telegram_user:
            logger.warning("Call config incomplete (no Telegram user); cannot call.")
            return False

        try:
            response = requests.get(
                self.API_URL,
                params={
                    "user": cfg.telegram_user,
                    "text": text[:256],
                    "lang": cfg.lang,
                    "rpt": cfg.repeats,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.error("Failed to place call: %s", exc)
            return False

        # CallMeBot answers 200 even on failures; check the body too.
        body = response.text or ""
        failed = re.search(
            r"ERROR|not authorized|wrong format", body, re.IGNORECASE
        )
        if response.status_code >= 400 or failed:
            logger.error(
                "CallMeBot call rejected (HTTP %s): %s",
                response.status_code,
                failed.group(0) if failed else body[:200],
            )
            return False
        logger.info("Call placed to %s", cfg.telegram_user)
        return True

    def call_auction_alert(
        self,
        auction: Auction,
        decision: AlertDecision,
        minutes_left: Optional[float] = None,
    ) -> bool:
        return self.call(build_call_text(auction, decision, minutes_left))
