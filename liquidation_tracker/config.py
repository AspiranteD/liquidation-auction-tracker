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
class AlertRules:
    """Thresholds that decide whether an auction is 'key' and worth an alert.

    The percentage ceilings apply to the TOTAL landed cost (bid + transport +
    VAT + B-Stock fee + RE) as a fraction of retail — the bid itself typically
    ends up around 5-10% of retail when the total lands at 12%.
    """

    max_total_cost_pct: float = 0.12               # ceiling for any lot
    electronics_max_total_pct: float = 0.15        # ceiling when the lot has electronics
    very_good_total_pct: float = 0.10              # <= this triggers the last-call reminder

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

    # Alerts fire as reminders before close, not when the auction appears.
    reminder_window_min: int = 30        # first reminder: <= 30 min to close
    final_reminder_window_min: int = 5   # last call: <= 5 min to close and very good

    countries: List[str] = field(default_factory=lambda: ["ES"])
    min_pieces: int = 0

    @classmethod
    def from_env(cls) -> "AlertRules":
        defaults = cls()
        return cls(
            max_total_cost_pct=_get_float(
                "ALERT_MAX_TOTAL_PCT", defaults.max_total_cost_pct
            ),
            electronics_max_total_pct=_get_float(
                "ALERT_ELECTRONICS_MAX_TOTAL_PCT", defaults.electronics_max_total_pct
            ),
            very_good_total_pct=_get_float(
                "ALERT_VERY_GOOD_TOTAL_PCT", defaults.very_good_total_pct
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
            reminder_window_min=_get_int(
                "REMINDER_WINDOW_MINUTES", defaults.reminder_window_min
            ),
            final_reminder_window_min=_get_int(
                "FINAL_REMINDER_WINDOW_MINUTES", defaults.final_reminder_window_min
            ),
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
            rules=rules,
        )
