"""Configuration loaded from environment variables (.env supported)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    username: Optional[str] = None
    password: Optional[str] = None
    sender: Optional[str] = None
    recipients: List[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "EmailConfig":
        username = os.getenv("SMTP_USERNAME")
        return cls(
            enabled=os.getenv("EMAIL_ALERTS_ENABLED", "false").lower() == "true",
            smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=_get_int("SMTP_PORT", 587),
            username=username,
            password=os.getenv("SMTP_PASSWORD"),
            sender=os.getenv("EMAIL_SENDER", username),
            recipients=_get_list("EMAIL_RECIPIENTS", []),
        )


def _get_int_list(name: str, default: List[int]) -> List[int]:
    raw = _get_list(name, [])
    if not raw:
        return list(default)
    values: List[int] = []
    for part in raw:
        try:
            values.append(int(part))
        except ValueError:
            continue
    return values or list(default)


@dataclass
class WhatsAppConfig:
    """CallMeBot WhatsApp alerts (https://www.callmebot.com/blog/free-api-whatsapp-messages/)."""

    enabled: bool = False
    phone: Optional[str] = None    # international format, e.g. +34600111222
    apikey: Optional[str] = None   # the key CallMeBot sends you on WhatsApp

    @classmethod
    def from_env(cls) -> "WhatsAppConfig":
        return cls(
            enabled=os.getenv("WHATSAPP_ALERTS_ENABLED", "false").lower() == "true",
            phone=os.getenv("CALLMEBOT_PHONE"),
            apikey=os.getenv("CALLMEBOT_APIKEY"),
        )


@dataclass
class CallConfig:
    """Voice-call escalation through CallMeBot's Telegram call API.

    Free, but needs a one-time setup: install Telegram and send /start to
    @CallMeBot_txtbot so it is allowed to call you. The call rings on
    Telegram and a TTS voice reads the alert.
    """

    enabled: bool = False
    telegram_user: Optional[str] = None  # "@usuario" or +34... phone
    lang: str = "es-ES-Standard-A"
    repeats: int = 2                     # times the message is read

    @classmethod
    def from_env(cls) -> "CallConfig":
        defaults = cls()
        return cls(
            enabled=os.getenv("CALL_ALERTS_ENABLED", "false").lower() == "true",
            telegram_user=os.getenv("CALLMEBOT_TELEGRAM_USER"),
            lang=os.getenv("CALL_LANG", defaults.lang),
            repeats=_get_int("CALL_REPEAT", defaults.repeats),
        )


@dataclass
class AlertRules:
    """Thresholds that decide whether an auction is 'key' and worth an alert.

    No more fixed percentage ceilings: the recommended bid is derived from the
    macro recovery study (recovery / ``bid_multiple`` of retail). An auction is
    over the limit when its current bid exceeds that recommended bid.
    """

    # Target gross markup: recommended landed cost = recovery / bid_multiple of
    # retail (a 3x box multiple). Replaces the old 12%/15% rules.
    bid_multiple: float = 3.0
    # The lot is a "very good" last-call when its current bid is at or below
    # this fraction of the recommended bid (lots of headroom left).
    very_good_headroom_fraction: float = 0.5

    # Minimum retail value (EUR) per lot family. Families not listed here are
    # never alerted.
    min_retail_by_type: Dict[str, float] = field(
        default_factory=lambda: {
            "4 Pallets": 20000.0,
            "Small Truckload": 50000.0,
            "Truckload": 100000.0,
        }
    )

    # Title keywords that mark a lot as electronics (iPhones, Macs, lenses...).
    electronics_keywords: List[str] = field(
        default_factory=lambda: [
            "Wireless",
            "PC Goods",
            "Camera",
            "Computers",
            "Electronics",
            "Home Entertainment",
        ]
    )

    # Reminder ladder: minutes-to-close thresholds, one WhatsApp per stage
    # as the auction approaches its close (alerts only, never on listing).
    reminder_stages: List[int] = field(default_factory=lambda: [30, 15, 10, 5])
    # At or under this many minutes to close, escalate with a voice call.
    call_at_minutes: int = 5

    countries: List[str] = field(default_factory=lambda: ["ES"])
    min_pieces: int = 0

    @classmethod
    def from_env(cls) -> "AlertRules":
        defaults = cls()
        return cls(
            bid_multiple=_get_float("ALERT_BID_MULTIPLE", defaults.bid_multiple),
            very_good_headroom_fraction=_get_float(
                "ALERT_VERY_GOOD_HEADROOM", defaults.very_good_headroom_fraction
            ),
            min_retail_by_type={
                "4 Pallets": _get_float("ALERT_MIN_RETAIL_4_PALLETS", 20000.0),
                "Small Truckload": _get_float(
                    "ALERT_MIN_RETAIL_SMALL_TRUCKLOAD", 50000.0
                ),
                "Truckload": _get_float("ALERT_MIN_RETAIL_TRUCKLOAD", 100000.0),
            },
            electronics_keywords=_get_list(
                "ELECTRONICS_KEYWORDS", defaults.electronics_keywords
            ),
            reminder_stages=sorted(
                _get_int_list("REMINDER_STAGES", defaults.reminder_stages),
                reverse=True,
            ),
            call_at_minutes=_get_int("CALL_AT_MINUTES", defaults.call_at_minutes),
            countries=_get_list("MONITOR_COUNTRIES", ["ES"]),
            min_pieces=_get_int("ALERT_MIN_PIECES", 0),
        )


@dataclass
class Settings:
    db_path: str = "data/auctions.db"
    manifest_dir: str = "data/manifests"
    countries: List[str] = field(default_factory=lambda: ["ES"])
    email: EmailConfig = field(default_factory=EmailConfig)
    whatsapp: WhatsAppConfig = field(default_factory=WhatsAppConfig)
    call: CallConfig = field(default_factory=CallConfig)
    rules: AlertRules = field(default_factory=AlertRules)

    @classmethod
    def from_env(cls) -> "Settings":
        rules = AlertRules.from_env()
        return cls(
            db_path=os.getenv("DB_PATH", "data/auctions.db"),
            manifest_dir=os.getenv("MANIFEST_DIR", "data/manifests"),
            countries=rules.countries,
            email=EmailConfig.from_env(),
            whatsapp=WhatsAppConfig.from_env(),
            call=CallConfig.from_env(),
            rules=rules,
        )
