"""Recovery model: blending, token estimation, and the recovery/multiple bid."""
from liquidation_tracker.calculator import BidCalculator
from liquidation_tracker.recovery import GroupAggregate, RecoveryModel


def _model() -> RecoveryModel:
    return RecoveryModel(
        global_recovery=0.24,
        by_department={
            "Electronics": {"recovery": 0.20, "n": 1000},
            "Toys": {"recovery": 0.35, "n": 1000},
            "Tiny": {"recovery": 0.90, "n": 5},  # below min_sample -> ignored
        },
        by_category={"Headphones": {"recovery": 0.16, "n": 500}},
        min_sample=30,
    )


def test_blended_recovery_weighted_by_retail():
    m = _model()
    groups = [
        GroupAggregate("Electronics", 8000.0),  # 20%
        GroupAggregate("Toys", 2000.0),         # 35%
    ]
    blend = m.blended(groups)
    # (8000*0.20 + 2000*0.35) / 10000 = 0.23
    assert abs(blend.recovery - 0.23) < 1e-6
    assert blend.coverage == 1.0


def test_unknown_group_falls_back_to_global():
    m = _model()
    blend = m.blended([GroupAggregate("Mystery", 1000.0)])
    assert abs(blend.recovery - 0.24) < 1e-6
    assert blend.coverage == 0.0


def test_small_sample_group_uses_global():
    m = _model()
    blend = m.blended([GroupAggregate("Tiny", 1000.0)])  # n=5 < 30
    assert abs(blend.recovery - 0.24) < 1e-6  # ignored -> global


def test_estimate_from_title_tokens_with_alias():
    m = _model()
    # "PC Goods" aliases to a department absent here -> global; "Toys" -> 0.35.
    blend = m.estimate_from_tokens(["Toys", "Mystery"])
    # (0.35 + 0.24) / 2 averaged equally
    assert abs(blend.recovery - (0.35 + 0.24) / 2) < 1e-6
    assert blend.coverage == 0.5


def test_recommend_bid_targets_recovery_over_multiple():
    m = _model()
    calc = BidCalculator()
    rec = m.recommend_bid(25000.0, 0.30, calc, "4 Pallets", country="ES", multiple=3.0)
    # landed cost should be recovery/multiple = 10% of retail
    assert abs(rec.breakdown.total_pct_of_retail - 0.10) < 0.001
    assert rec.expected_revenue == 25000.0 * 0.30


def test_recommend_bid_uses_country_transport():
    m = _model()
    calc = BidCalculator()
    es = m.recommend_bid(25000.0, 0.30, calc, "4 Pallets", country="ES")
    de = m.recommend_bid(25000.0, 0.30, calc, "4 Pallets", country="DE")
    # DE transport is dearer, so for the same landed-cost target the bid is lower.
    assert de.bid < es.bid


# --- El reloj de la madurez: la LLEGADA, no la compra (dueño, 2026-08-17) -----

def test_el_reloj_de_madurez_prefiere_la_llegada_y_no_inventa_fechas():
    """La regla que impide que la cohorte madura se llene de antigüedades falsas.

    Importa porque `purchase_date` NO es una compra (es el `date_in` de Amazon) y
    porque el alta tiene 33.315 filas selladas con el día en que corrió un script
    de carga: contar eso como llegada es lo que produjo curvas de recuperación
    plausibles y falsas.
    """
    import numpy as np
    import pandas as pd

    from scripts.build_recovery import SELLOS_ALTA, reloj_de_madurez

    alta = pd.to_datetime(pd.Series([
        "2026-04-20",   # alta normal -> manda la llegada
        "2025-12-22",   # sello -> cae al date_in del manifiesto
        "2025-12-22",   # sello Y date_in del mismo día -> no hay fecha
        "2026-02-18",   # el otro sello
    ]))
    date_in = pd.to_datetime(pd.Series([
        "2026-04-01", "2025-03-14", "2025-12-22", "2025-06-30",
    ]))

    reloj, origen = reloj_de_madurez(alta, date_in)

    assert list(origen) == ["llegada", "manifiesto", "sin fecha", "manifiesto"]
    assert reloj.iloc[0] == pd.Timestamp("2026-04-20")   # la llegada, no el date_in
    assert reloj.iloc[1] == pd.Timestamp("2025-03-14")   # fallback documentado
    assert pd.isna(reloj.iloc[2])                        # ⛔ no se inventa antigüedad
    assert reloj.iloc[3] == pd.Timestamp("2025-06-30")
    assert "2025-12-22" in SELLOS_ALTA and "2026-02-18" in SELLOS_ALTA
