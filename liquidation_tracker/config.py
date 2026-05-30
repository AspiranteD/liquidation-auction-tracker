"""Configuration loaded from environment variables (.env supported)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

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
class AlertRules:
    """Thresholds that decide whether an auction is 'key' and worth an email."""

    min_retail_value: float = 5000.0
    max_total_cost_pct: float = 0.30   # only alert if you can land it <= 30% of retail
    target_total_pct: float = 0.25     # the % used to compute the suggested max bid
    countries: List[str] = field(default_factory=lambda: ["ES"])
    min_pieces: int = 0

    @classmethod
    def from_env(cls) -> "AlertRules":
        return cls(
            min_retail_value=_get_float("ALERT_MIN_RETAIL", 5000.0),
            max_total_cost_pct=_get_float("ALERT_MAX_TOTAL_PCT", 0.30),
            target_total_pct=_get_float("BID_TARGET_TOTAL_PCT", 0.25),
            countries=_get_list("MONITOR_COUNTRIES", ["ES"]),
            min_pieces=_get_int("ALERT_MIN_PIECES", 0),
        )


@dataclass
class Settings:
    db_path: str = "data/auctions.db"
    manifest_dir: str = "data/manifests"
    countries: List[str] = field(default_factory=lambda: ["ES"])
    email: EmailConfig = field(default_factory=EmailConfig)
    rules: AlertRules = field(default_factory=AlertRules)

    @classmethod
    def from_env(cls) -> "Settings":
        rules = AlertRules.from_env()
        return cls(
            db_path=os.getenv("DB_PATH", "data/auctions.db"),
            manifest_dir=os.getenv("MANIFEST_DIR", "data/manifests"),
            countries=rules.countries,
            email=EmailConfig.from_env(),
            rules=rules,
        )
