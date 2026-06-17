from datetime import datetime, timezone

from liquidation_tracker import alerts
from liquidation_tracker.calculator import BidCalculator
from liquidation_tracker.config import AlertRules
from liquidation_tracker.models import Auction
from liquidation_tracker.recovery import RecoveryModel


CALC = BidCalculator()
RULES = AlertRules()
# Deterministic recovery model: the test lot's categories recover 30%, so the
# recommended landed cost is 30%/3 = 10% of retail. Unknown categories fall
# back to the 24% global.
REC = RecoveryModel(
    global_recovery=0.24,
    by_department={},
    by_category={
        "Home Goods": {"recovery": 0.30, "n": 100},
        "Kitchen": {"recovery": 0.30, "n": 100},
        "Wireless": {"recovery": 0.30, "n": 100},
        "PC Goods": {"recovery": 0.30, "n": 100},
    },
    min_sample=30,
)


def _auction(**overrides) -> Auction:
    base = dict(
        auction_id=1,
        title=(
            "4 Pallets of Home Goods, Kitchen & More, 500 Pieces, "
            "Customer Returns, Total Retail €25,000, ES Stock"
        ),
        url="https://bstock.com/amazoneu/auction/auction/view/id/1/",
        country="ES",
        lot_type="4 Pallets",
        retail_value=25000.0,
        pieces=500,
        current_bid=500.0,
        end_time=datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return Auction(**base)


def _eval(auction: Auction) -> alerts.AlertDecision:
    return alerts.evaluate(auction, RULES, CALC, REC)


def test_lot_family_normalization():
    assert alerts.lot_family("4 Pallets") == "4 Pallets"
    assert alerts.lot_family("4 Pallets De") == "4 Pallets"
    assert alerts.lot_family("Small Truckload") == "Small Truckload"
    assert alerts.lot_family("Truckload") == "Truckload"
    assert alerts.lot_family("Pallet") is None
    assert alerts.lot_family(None) is None


def test_lot_categories_from_title():
    cats = alerts.lot_categories(
        "4 Pallets of Home Goods, Kitchen & More, 500 Pieces, ES Stock"
    )
    assert cats == ["Home Goods", "Kitchen"]


def test_electronics_detection_by_title():
    electro = _auction(title="4 Pallets of Wireless, PC Goods & More, ES Stock")
    plain = _auction()
    assert alerts.is_electronics(electro, RULES) is True
    assert alerts.is_electronics(plain, RULES) is False


def test_recovery_estimated_from_title():
    decision = _eval(_auction())
    assert abs(decision.recovery - 0.30) < 1e-6  # both categories at 30%


def test_recovery_falls_back_to_global_for_unknown_categories():
    decision = _eval(_auction(title="4 Pallets of Mystery Stuff, 10 Pieces, ES Stock"))
    assert abs(decision.recovery - 0.24) < 1e-6


def test_recommended_bid_targets_recovery_over_multiple():
    decision = _eval(_auction())
    assert decision.breakdown is not None
    # Landed cost of the recommended bid equals recovery / bid_multiple (10%).
    assert abs(decision.breakdown.total_pct_of_retail - 0.30 / 3) < 0.001


def test_key_when_bid_under_recommended():
    decision = _eval(_auction())  # bid 500, recommended ~1800
    assert decision.is_key is True
    assert decision.recommended_bid > 500
    assert decision.current_bid_pct is not None


def test_over_limit_when_bid_above_recommended():
    rec = _eval(_auction()).recommended_bid
    decision = _eval(_auction(current_bid=rec + 500))
    assert decision.is_key is False
    assert decision.over_limit is True
    assert any("recommended" in r for r in decision.reasons)


def test_min_retail_per_lot_type():
    assert _eval(_auction(retail_value=21000.0)).is_key is True

    small = _auction(lot_type="Small Truckload", retail_value=21000.0)
    assert _eval(small).is_key is False

    truck = _auction(lot_type="Truckload", retail_value=99000.0)
    assert _eval(truck).is_key is False

    big_truck = _auction(lot_type="Truckload", retail_value=120000.0)
    assert _eval(big_truck).is_key is True


def test_unmonitored_lot_type_never_key():
    assert _eval(_auction(lot_type="Pallet")).is_key is False


def test_no_bid_counts_as_zero():
    decision = _eval(_auction(current_bid=None))
    assert decision.is_key is True
    assert decision.current_bid_pct is not None


def test_very_good_with_lots_of_headroom():
    good = _eval(_auction(current_bid=500.0))
    assert good.very_good is True

    # A bid just under the recommended max is key but no longer "very good".
    borderline = _eval(_auction(current_bid=good.recommended_bid - 50))
    assert borderline.is_key is True
    assert borderline.very_good is False
