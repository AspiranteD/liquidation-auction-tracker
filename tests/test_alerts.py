from datetime import datetime, timezone

from liquidation_tracker import alerts
from liquidation_tracker.calculator import BidCalculator
from liquidation_tracker.config import AlertRules
from liquidation_tracker.models import Auction


CALC = BidCalculator()
RULES = AlertRules()


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


def test_lot_family_normalization():
    assert alerts.lot_family("4 Pallets") == "4 Pallets"
    assert alerts.lot_family("4 Pallets De") == "4 Pallets"
    assert alerts.lot_family("Small Truckload") == "Small Truckload"
    assert alerts.lot_family("Truckload") == "Truckload"
    assert alerts.lot_family("Pallet") is None
    assert alerts.lot_family(None) is None


def test_electronics_detection_by_title():
    electro = _auction(title="4 Pallets of Wireless, PC Goods & More, ES Stock")
    plain = _auction()
    assert alerts.is_electronics(electro, RULES) is True
    assert alerts.is_electronics(plain, RULES) is False


def test_key_when_under_12_pct():
    # bid 500 on 25k retail -> total ~ (500+318.99)*... well under 12%
    decision = alerts.evaluate(_auction(), RULES, CALC)
    assert decision.is_key is True
    assert decision.threshold_pct == RULES.max_total_cost_pct
    assert decision.current_total_pct < 0.12


def test_not_key_when_over_12_pct():
    # bid 2600 on 25k retail -> total > 12%
    decision = alerts.evaluate(_auction(current_bid=2600.0), RULES, CALC)
    assert decision.is_key is False
    assert any("implies" in r for r in decision.reasons)


def test_electronics_raises_threshold_to_15_pct():
    title = (
        "4 Pallets of Wireless, PC Goods & More, 400 Pieces, "
        "Customer Returns, Total Retail €25,000, ES Stock"
    )
    # bid 2300 on 25k retail -> total ~13.6%: fails at 12% but passes at 15%
    plain = alerts.evaluate(_auction(current_bid=2300.0), RULES, CALC)
    assert plain.is_key is False

    decision = alerts.evaluate(
        _auction(title=title, current_bid=2300.0), RULES, CALC
    )
    assert decision.electronics is True
    assert decision.threshold_pct == RULES.electronics_max_total_pct
    assert decision.is_key is True


def test_min_retail_per_lot_type():
    # 20k retail is enough for 4 Pallets...
    four_pallets = _auction(retail_value=21000.0)
    assert alerts.evaluate(four_pallets, RULES, CALC).is_key is True

    # ...but not for Small Truckload (needs 50k) nor Truckload (needs 100k)
    small = _auction(lot_type="Small Truckload", retail_value=21000.0)
    assert alerts.evaluate(small, RULES, CALC).is_key is False

    truck = _auction(lot_type="Truckload", retail_value=99000.0)
    assert alerts.evaluate(truck, RULES, CALC).is_key is False

    big_truck = _auction(lot_type="Truckload", retail_value=120000.0)
    assert alerts.evaluate(big_truck, RULES, CALC).is_key is True


def test_unmonitored_lot_type_never_key():
    decision = alerts.evaluate(_auction(lot_type="Pallet"), RULES, CALC)
    assert decision.is_key is False


def test_no_bid_counts_as_zero():
    decision = alerts.evaluate(_auction(current_bid=None), RULES, CALC)
    assert decision.is_key is True
    assert decision.current_total_pct is not None


def test_very_good_under_10_pct():
    good = alerts.evaluate(_auction(current_bid=500.0), RULES, CALC)
    assert good.very_good is True

    # bid 1800 on 25k -> total ~11.1%: key but not very good
    borderline = alerts.evaluate(_auction(current_bid=1800.0), RULES, CALC)
    assert borderline.is_key is True
    assert borderline.very_good is False


def test_suggested_bid_targets_threshold():
    decision = alerts.evaluate(_auction(), RULES, CALC)
    assert decision.breakdown is not None
    # Landed cost of the suggested bid must equal the 12% ceiling
    assert abs(decision.breakdown.total_pct_of_retail - 0.12) < 0.001
    # ...which puts the bid itself in the user's expected 5-10% band
    assert 0.05 < decision.breakdown.bid_pct_of_retail < 0.10
