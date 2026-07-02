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
