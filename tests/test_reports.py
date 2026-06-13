import os
from datetime import datetime, timedelta, timezone

from liquidation_tracker import alerts, insights, reports
from liquidation_tracker.calculator import BidCalculator
from liquidation_tracker.config import AlertRules
from liquidation_tracker.models import Auction, ManifestItem


def _decide(auction: Auction) -> alerts.AlertDecision:
    return alerts.evaluate(auction, AlertRules(), BidCalculator())


def _items():
    return [
        ManifestItem(
            description='LG OLED TV 55" 4K',
            category="Electronics",
            subcategory="TVs",
            department="Electronics",
            condition="Defective",
            qty=1,
            unit_retail=900.0,
            box_id="BOX1",
            pallet_id="PAL1",
            asin="B0TV",
        ),
        ManifestItem(
            description="Apple iPhone 16 128GB",
            category="Wireless",
            subcategory="Phones",
            department="Wireless",
            condition="Customer Damage",
            qty=1,
            unit_retail=14.0,
            box_id="BOX1",
            pallet_id="PAL1",
            asin="B0IP",
        ),
        ManifestItem(
            description="Sartén antiadherente 24cm",
            category="Kitchen",
            subcategory="Cookware",
            department="Kitchen",
            condition="Customer Returns",
            qty=2,
            unit_retail=20.0,
            box_id="BOX2",
            pallet_id="PAL1",
            asin="B0SA",
        ),
    ]


def _good_items():
    """A non-TV-heavy lot with a sure giveaway -> 'INTERESA'."""
    items = []
    # A real giveaway: iPhone declared at 14 EUR.
    items.append(ManifestItem(
        description="Apple iPhone 16 128GB", department="Wireless",
        condition="Customer Damage", qty=1, unit_retail=14.0,
        box_id="BOX1", pallet_id="PAL1", asin="B0IP",
    ))
    # Filler so TVs are a small share.
    for n in range(40):
        items.append(ManifestItem(
            description=f"Artículo de cocina {n}", department="Kitchen",
            condition="Customer Returns", qty=1, unit_retail=50.0,
            box_id="BOX1", pallet_id="PAL1",
        ))
    return items


def _auction(retail=25000.0, bid=500.0, minutes_to_close=200) -> Auction:
    return Auction(
        auction_id=99999,
        title="4 Pallets of Home Goods, 41 Pieces, Total Retail €25,000, ES Stock",
        url="https://bstock.com/amazoneu/auction/auction/view/id/99999/",
        country="ES",
        lot_type="4 Pallets",
        retail_value=retail,
        pieces=41,
        current_bid=bid,
        end_time=datetime.now(timezone.utc) + timedelta(minutes=minutes_to_close),
    )


def test_render_pdf_creates_file(tmp_path):
    result = insights.deep_analyze(_items(), label="test_pdf")
    path = str(tmp_path / "lot.pdf")
    reports.render_pdf(result, path, _auction())
    assert os.path.exists(path)
    with open(path, "rb") as fh:
        assert fh.read(5) == b"%PDF-"


def test_digest_pdf_with_failures(tmp_path):
    ok = reports.LotReport(
        auction=_auction(),
        insights=insights.deep_analyze(_items(), label="ok"),
    )
    bad = reports.LotReport(auction=_auction(), error="manifiesto no disponible")
    path = str(tmp_path / "digest.pdf")
    reports.build_digest_pdf([ok, bad], path)
    assert os.path.exists(path)
    assert os.path.getsize(path) > 1000


def test_whatsapp_lot_summary_leads_with_verdict():
    auction = _auction(minutes_to_close=200)
    report = reports.LotReport(
        auction=auction,
        insights=insights.deep_analyze(_good_items(), label="lot"),
        decision=_decide(auction),
        is_new=True,
    )
    text = reports.build_whatsapp_lot_summary(report)
    assert "#99999" in text
    assert "INTERESA" in text          # verdict headline
    assert "PROVISIONAL" in text       # far from close -> honest price note
    assert "email" in text.lower()


def test_verdict_flojo_when_tv_heavy():
    tv_lot = [
        ManifestItem(description='LG OLED TV 55" 4K', category="Electronics",
                     subcategory="TVs", department="Electronics", qty=1,
                     unit_retail=900.0, box_id="B", pallet_id="P"),
        ManifestItem(description="Sartén 24cm", department="Kitchen", qty=1,
                     unit_retail=60.0, box_id="B", pallet_id="P"),
    ]
    auction = _auction()
    report = reports.LotReport(
        auction=auction, insights=insights.deep_analyze(tv_lot, label="tv"),
        decision=_decide(auction),
    )
    level, label, _ = reports.lot_verdict(report)
    assert level == "🔴"
    assert "TVs" in label


def test_price_status_provisional_vs_reliable():
    far = reports.LotReport(auction=_auction(minutes_to_close=200),
                            decision=_decide(_auction(minutes_to_close=200)))
    assert "PROVISIONAL" in reports.price_status(far)

    near_auction = _auction(minutes_to_close=20)
    near = reports.LotReport(auction=near_auction, decision=_decide(near_auction))
    status = reports.price_status(near)
    assert "fiable" in status and "PROVISIONAL" not in status


def test_state_retry_cooldown(tmp_path):
    now = datetime.now()
    assert reports._should_retry(None, now) is True
    assert reports._should_retry({"status": "done"}, now) is False
    recent = {"status": "failed", "last_attempt": now.isoformat()}
    assert reports._should_retry(recent, now) is False
    old = {"status": "failed", "last_attempt": "2020-01-01T00:00:00"}
    assert reports._should_retry(old, now) is True
