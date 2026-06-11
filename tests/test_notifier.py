from datetime import datetime, timezone

from liquidation_tracker.alerts import AlertDecision
from liquidation_tracker.calculator import BidCalculator
from liquidation_tracker.config import WhatsAppConfig
from liquidation_tracker.models import Auction
from liquidation_tracker.notifier import WhatsAppNotifier, build_whatsapp_body


def _auction() -> Auction:
    return Auction(
        auction_id=50868,
        title=(
            "Small Truckload of Home Goods, Lawn and Garden & More, 365 Pieces, "
            "Customer Returns, Total Retail €16,404, ES Stock"
        ),
        url="https://bstock.com/amazoneu/auction/auction/view/id/50868/",
        country="ES",
        lot_type="Small Truckload",
        retail_value=16404.0,
        pieces=365,
        current_bid=747.0,
        end_time=datetime(2026, 6, 11, 13, 23, tzinfo=timezone.utc),
    )


def _decision(auction: Auction) -> AlertDecision:
    calc = BidCalculator()
    breakdown = calc.max_bid_for_retail_pct(
        auction.retail_value, 0.12, auction.lot_type
    )
    current = calc.cost_breakdown_for_bid(
        auction.current_bid or 0.0, auction.lot_type, retail_value=auction.retail_value
    )
    return AlertDecision(
        is_key=True,
        reasons=[],
        breakdown=breakdown,
        threshold_pct=0.12,
        current_total_pct=current.total_pct_of_retail,
    )


def test_whatsapp_body_contains_key_facts():
    auction = _auction()
    body = build_whatsapp_body(auction, _decision(auction), "t30", 28.0)
    assert "Cierra en 28 min" in body
    assert "ES" in body
    assert "Small Truckload" in body
    assert "16,404" in body
    assert auction.url in body
    assert "11/06" in body
    assert "umbral 12%" in body


def test_whatsapp_body_last_call():
    auction = _auction()
    body = build_whatsapp_body(auction, _decision(auction), "t5", 4.0)
    assert "ÚLTIMA LLAMADA" in body
    assert "cierra en 4 min" in body


def test_whatsapp_disabled_does_not_send():
    notifier = WhatsAppNotifier(WhatsAppConfig(enabled=False))
    assert notifier.send("hola") is False


def test_whatsapp_incomplete_config_does_not_send():
    notifier = WhatsAppNotifier(WhatsAppConfig(enabled=True, phone="+34600111222"))
    assert notifier.send("hola") is False


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "Message queued."):
        self.status_code = status_code
        self.text = text


def test_whatsapp_send_success(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse()

    monkeypatch.setattr("liquidation_tracker.notifier.requests.get", fake_get)
    cfg = WhatsAppConfig(enabled=True, phone="+34600111222", apikey="abc123")
    assert WhatsAppNotifier(cfg).send("hola") is True
    assert captured["url"] == WhatsAppNotifier.API_URL
    assert captured["params"]["phone"] == "+34600111222"
    assert captured["params"]["apikey"] == "abc123"
    assert captured["params"]["text"] == "hola"


def test_whatsapp_send_invalid_apikey(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(text="APIKey is invalid or not authorized")

    monkeypatch.setattr("liquidation_tracker.notifier.requests.get", fake_get)
    cfg = WhatsAppConfig(enabled=True, phone="+34600111222", apikey="bad")
    assert WhatsAppNotifier(cfg).send("hola") is False
