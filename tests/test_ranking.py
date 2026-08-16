"""Tests for the manifest-weighted lot ranking (ranking.py)."""
from liquidation_tracker import analyzer, insights, ranking
from liquidation_tracker.calculator import BidCalculator
from liquidation_tracker.insights import GroupStats
from liquidation_tracker.models import Auction
from liquidation_tracker.recovery import RecoveryModel


def _model() -> RecoveryModel:
    return RecoveryModel(
        global_recovery=0.25,
        by_department={
            "PC": {"recovery": 0.40, "n": 500},
            "Electronics": {"recovery": 0.20, "n": 500},
            "Rare": {"recovery": 0.90, "n": 5},  # below min_sample -> ignored
        },
        by_category={},
        min_sample=30,
    )


def test_blended_is_retail_weighted():
    groups = [GroupStats(name="PC", retail=750.0),
              GroupStats(name="Electronics", retail=250.0)]
    blend = _model().blended(groups)
    assert abs(blend.recovery - 0.35) < 1e-6   # .75*.40 + .25*.20
    assert abs(blend.coverage - 1.0) < 1e-6


def test_unmatched_department_falls_back_to_global():
    groups = [GroupStats(name="PC", retail=500.0),
              GroupStats(name="Unknown", retail=500.0)]
    blend = _model().blended(groups)
    assert abs(blend.recovery - 0.325) < 1e-6  # .5*.40 + .5*.25(global)
    assert abs(blend.coverage - 0.5) < 1e-6


def test_low_sample_department_is_not_trusted():
    groups = [GroupStats(name="Rare", retail=1000.0)]   # n=5 < min_sample
    blend = _model().blended(groups)
    assert abs(blend.recovery - 0.25) < 1e-6            # falls back to global
    assert abs(blend.coverage - 0.0) < 1e-6


def test_rank_lot_score_is_revenue_plus_hidden():
    items = analyzer.parse_manifest("data/sample_manifest.csv")
    result = insights.deep_analyze(items, label="sample")
    auction = Auction(
        auction_id=1, title="sample", url="", country="ES",
        lot_type="4 Pallets", retail_value=result.total_retail, current_bid=0.0,
    )
    lr = ranking.rank_lot(auction, result, _model(), BidCalculator(), multiple=3.0)
    assert abs(lr.score - (lr.expected_revenue + lr.hidden_value)) < 0.02
    assert 0.0 <= lr.recovery <= 1.0
    assert 0.0 <= lr.coverage <= 1.0
    assert lr.recommended_bid >= 0.0
    assert lr.headroom == round(lr.recommended_bid - lr.current_bid, 2)


def _item(**overrides):
    from liquidation_tracker.models import ManifestItem
    base = dict(description="Generic gadget", category="Home", subcategory="Misc",
                department="Home", condition="Customer Returns", qty=1,
                unit_retail=25.0, box_id="BOX1", pallet_id="PAL1", asin="B000000001")
    base.update(overrides)
    return ManifestItem(**base)


def test_las_tvs_no_pesan_en_el_recovery_del_lote():
    """Regla del dueño (2026-08-17): las TVs van APARTE y no entran en ninguna
    métrica. Un lote con paneles no puede heredar el recovery hundido de
    «Home Entertainment» ni pesar su retail en la mezcla."""
    items = [
        _item(description='Samsung Smart TV 55"', category="Televisions",
              subcategory='TVs 51"-60"', department="Home Entertainment",
              unit_retail=800.0, asin="B0TV0000001"),
        _item(description="Chromecast", category="Streaming Media Players",
              subcategory="Streaming", department="Home Entertainment",
              unit_retail=200.0, asin="B0CC0000002"),
        _item(description="Laptop", category="Notebooks", subcategory="Laptops",
              department="PC", unit_retail=1000.0, asin="B0PC0000003"),
    ]
    result = insights.deep_analyze(items, label="tv-lot")
    assert result.tv_units == 1 and result.effective_retail == 1200.0

    groups = {g.name: g.retail for g in ranking.groups_without_tvs(result)}
    # La tele sale de su departamento; el Chromecast se queda.
    assert groups == {"PC": 1000.0, "Home Entertainment": 200.0}

    model = RecoveryModel(
        global_recovery=0.25,
        by_department={"PC": {"recovery": 0.40, "n": 500},
                       "Home Entertainment": {"recovery": 0.30, "n": 500}},
        by_category={}, min_sample=30,
    )
    auction = Auction(auction_id=1, title="tv-lot", url="", country="ES",
                      lot_type="4 Pallets", retail_value=2000.0, current_bid=0.0)
    lr = ranking.rank_lot(auction, result, model, BidCalculator(), multiple=3.0)
    # Mezcla SIN la tele: (1000*.40 + 200*.30)/1200 = 0,3833 — con la tele dentro
    # habría salido (1000*.40 + 1000*.30)/2000 = 0,35.
    assert abs(lr.recovery - 0.3833) < 1e-3
    assert abs(lr.expected_revenue - 1200.0 * (1000 * .40 + 200 * .30) / 1200) < 0.02
    # Y la tele tampoco es un «regalado» ni un artículo de valor del lote.
    assert lr.hidden_value == 0.0
    assert all(i.category != "Televisions" for i in result.top_items)
