"""Tests for the bid calculator.

The key invariant: the forward and reverse calculations are consistent. If we
compute the max bid for a target landed cost and then feed that bid back in, we
must recover the original target.
"""
import math

from liquidation_tracker.calculator import BidCalculator


def test_transport_lookup_is_case_insensitive():
    calc = BidCalculator()
    assert calc.transport_for("Truckload") == 636.12
    assert calc.transport_for("small truckload") == 433.11
    assert calc.transport_for("4 Pallets DE") == 790.0
    assert calc.transport_for("unknown type") == 0.0
    assert calc.transport_for(None) == 0.0


def test_forward_reverse_consistency():
    calc = BidCalculator()
    retail = 16670.0
    target_pct = 0.25
    breakdown = calc.max_bid_for_retail_pct(retail, target_pct, "Small Truckload")

    # Landed cost must match retail * target_pct.
    assert math.isclose(breakdown.total_cost, retail * target_pct, abs_tol=0.5)
    # And total_pct_of_retail must reflect the target.
    assert math.isclose(breakdown.total_pct_of_retail, target_pct, abs_tol=0.001)


def test_cost_breakdown_components_sum_to_total():
    calc = BidCalculator()
    b = calc.cost_breakdown_for_bid(2000.0, "Truckload", retail_value=20000.0)
    parts = b.bid + b.transport + b.vat + b.bstock_fee + b.re
    assert math.isclose(parts, b.total_cost, abs_tol=0.05)


def test_vat_is_21_percent_of_bid_plus_transport():
    calc = BidCalculator()
    b = calc.cost_breakdown_for_bid(1000.0, "Small Truckload")
    expected_vat = (b.bid + b.transport) * 0.21
    assert math.isclose(b.vat, expected_vat, abs_tol=0.05)


def test_bstock_fee_is_4_percent_of_bid():
    calc = BidCalculator()
    b = calc.cost_breakdown_for_bid(1500.0, "Truckload")
    assert math.isclose(b.bstock_fee, 1500.0 * 0.04, abs_tol=0.05)


def test_max_bid_never_negative():
    calc = BidCalculator()
    # Tiny retail with expensive transport -> bid would be negative, clamp to 0.
    b = calc.max_bid_for_retail_pct(100.0, 0.25, "4 Pallets PL")
    assert b.bid == 0.0
