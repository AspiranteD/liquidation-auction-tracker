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


def test_bstock_fee_is_5_percent_of_bid():
    # B-Stock raised the buyer premium 4% -> 5% in 2026. Read straight off a live
    # lot page on 2026-08-05 (hidden field `buyersPremiumPercent` = 0.05), and it
    # matches the backend (scripts/services/bstock/calculator.py). Understating it
    # inflates every max bid.
    calc = BidCalculator()
    b = calc.cost_breakdown_for_bid(1500.0, "Truckload")
    assert math.isclose(b.bstock_fee, 1500.0 * 0.05, abs_tol=0.05)


def test_part_load_pallets_cost_the_same_transport_as_four():
    # A 2- or 3-pallet lot rides the same truck slot as a 4-pallet one, so it is
    # billed the same (dueño, 2026-08-05). Before this, "2 Pallets" matched no
    # tariff, transport silently counted 0 EUR and the max bid came out inflated.
    calc = BidCalculator()
    cuatro = calc.transport_for("4 Pallets")
    assert calc.transport_for("2 Pallets") == cuatro
    assert calc.transport_for("3 Pallets") == cuatro
    assert calc.transport_for("2 Pallets DE") == calc.transport_for("4 Pallets DE")
    # ...cualquier otro «N Pallets» (5, 6, 9) coge la tarifa de 4 como SUELO (espejo del
    # backend desde el 2026-08-17: 0 € inflaría la puja); un tipo que NO es de palés
    # sigue a 0, como antes.
    assert calc.transport_for("9 Pallets") == cuatro
    assert calc.transport_for("Container") == 0.0


def test_max_bid_never_negative():
    calc = BidCalculator()
    # Tiny retail with expensive transport -> bid would be negative, clamp to 0.
    b = calc.max_bid_for_retail_pct(100.0, 0.25, "4 Pallets PL")
    assert b.bid == 0.0


# ---------------------------------------------------------------------------
# 2026-08-17: espejo con scripts/services/bstock/calculator.py de reusalia-backend
# (el tracker está ABSORBIDO: la calculadora que manda es la del backend; esta es su copia).
# ---------------------------------------------------------------------------
def test_espejo_backend_n_pallets_y_pais():
    from liquidation_tracker.calculator import BidCalculator, DEFAULT_TRANSPORT_COSTS
    calc = BidCalculator()
    # 5-6 palés: tarifa de 4 como suelo (antes daba 0 € ⇒ puja inflada)
    assert calc.transport_for("6 Pallets") == DEFAULT_TRANSPORT_COSTS["4 Pallets"]
    assert calc.transport_for("5 pallets") == DEFAULT_TRANSPORT_COSTS["4 Pallets"]
    # país en 4 palés
    assert calc.transport_for("4 Pallets", "DE") == 790.0
    assert calc.transport_for("2 Pallets", "PL") == 900.0
    assert calc.transport_for("4 Pallets", "ES") == 318.99
    assert calc.transport_for("4 Pallets DE") == 790.0   # clave precompuesta (transport_key)
    b = calc.max_bid_for_retail_pct(20000.0, 0.10, "4 Pallets", country="DE")
    assert b.transport == 790.0 and b.bid > 0
    assert abs(b.total_pct_of_retail - 0.10) < 0.0005
