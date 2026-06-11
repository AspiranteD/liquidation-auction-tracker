import os
from datetime import datetime, timezone

from liquidation_tracker import insights, reports
from liquidation_tracker.models import Auction, ManifestItem


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


def _auction() -> Auction:
    return Auction(
        auction_id=99999,
        title="4 Pallets of Test Goods, 4 Pieces, Total Retail €954, ES Stock",
        url="https://bstock.com/amazoneu/auction/auction/view/id/99999/",
        country="ES",
        lot_type="4 Pallets",
        retail_value=954.0,
        pieces=4,
        end_time=datetime(2026, 6, 11, 14, 0, tzinfo=timezone.utc),
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


def test_whatsapp_lot_summary_contents():
    report = reports.LotReport(
        auction=_auction(),
        insights=insights.deep_analyze(_items(), label="lot"),
        is_new=True,
    )
    text = reports.build_whatsapp_lot_summary(report)
    assert "#99999" in text
    assert "TVs: 1" in text
    assert "Regalados" in text
    assert "email" in text.lower()


def test_state_retry_cooldown(tmp_path):
    now = datetime.now()
    assert reports._should_retry(None, now) is True
    assert reports._should_retry({"status": "done"}, now) is False
    recent = {"status": "failed", "last_attempt": now.isoformat()}
    assert reports._should_retry(recent, now) is False
    old = {"status": "failed", "last_attempt": "2020-01-01T00:00:00"}
    assert reports._should_retry(old, now) is True
