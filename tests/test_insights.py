from liquidation_tracker import insights
from liquidation_tracker.models import ManifestItem


def _item(**overrides) -> ManifestItem:
    base = dict(
        description="Generic gadget",
        category="Home",
        subcategory="Misc",
        department="Home",
        condition="Customer Returns",
        qty=1,
        unit_retail=25.0,
        box_id="BOX1",
        pallet_id="PAL1",
        asin="B000000001",
    )
    base.update(overrides)
    return ManifestItem(**base)


# --- TVs ---------------------------------------------------------------

def test_tv_detected_by_size_and_keyword():
    items = [_item(description='Samsung Smart TV 55" Crystal UHD 4K', unit_retail=550.0)]
    tvs = insights.find_tvs(items)
    assert len(tvs) == 1
    assert tvs[0].confidence == "seguro"


def test_tv_detected_by_category():
    items = [
        _item(
            description="Modelo X100",
            category="Electronics",
            subcategory="TVs",
            unit_retail=400.0,
        )
    ]
    assert insights.find_tvs(items)[0].confidence == "seguro"


def test_tv_accessory_not_counted():
    items = [
        _item(description="Soporte de pared para TV 55 pulgadas"),
        _item(description="Fire TV Stick 4K"),
        _item(description="Mando a distancia universal TV"),
    ]
    assert insights.find_tvs(items) == []


def test_tv_loss_subtracted_from_effective_retail():
    items = [
        _item(description='LG OLED TV 65" C3', unit_retail=1500.0),
        _item(description="Taladro inalambrico", unit_retail=100.0),
    ]
    result = insights.deep_analyze(items)
    assert result.tv_loss_retail == 1500.0
    assert result.effective_retail == 100.0


# --- Giveaways ----------------------------------------------------------

def test_iphone_at_12_eur_is_sure_giveaway():
    items = [_item(description="Apple iPhone 16 Pro 256GB Titanio", unit_retail=12.0)]
    found = insights.find_giveaways(items)
    assert len(found) == 1
    assert found[0].tier == "seguro"
    assert "amazon.es" in found[0].amazon_url


def test_macbook_at_150_eur_is_doubtful():
    items = [_item(description="Apple MacBook Air M3 13in", unit_retail=150.0)]
    found = insights.find_giveaways(items)
    assert len(found) == 1
    assert found[0].tier == "dudoso"


def test_iphone_case_is_not_a_giveaway():
    items = [_item(description="Funda iPhone 16 silicona", unit_retail=12.0)]
    assert insights.find_giveaways(items) == []


def test_premium_at_plausible_price_not_flagged():
    items = [_item(description="Apple iPhone 15 128GB", unit_retail=450.0)]
    assert insights.find_giveaways(items) == []


def test_surveillance_camera_not_flagged_as_lens():
    items = [
        _item(
            description="Dahua Cámara de cúpula videovigilancia objetivo 2.8mm",
            unit_retail=57.0,
        )
    ]
    assert insights.find_giveaways(items) == []


def test_compatibility_mention_not_flagged():
    items = [
        _item(description="Lápiz de Repuesto para Samsung Galaxy S25 Ultra", unit_retail=17.0),
        _item(description="Disquetera externa USB compatible con MacBook Windows", unit_retail=14.0),
        _item(description="Auriculares gaming inalámbricos para PS5 y PC", unit_retail=36.0),
    ]
    assert insights.find_giveaways(items) == []


def test_cheap_tv_lookalike_downgraded_to_posible():
    items = [
        _item(description="Convertidor HDMI 4K a 30Hz para TV", unit_retail=24.0),
        _item(description="Supporto TV a Parete Fisso 55 pollici", unit_retail=18.0),
    ]
    tvs = insights.find_tvs(items)
    assert not any(t.confidence == "seguro" for t in tvs)


def test_real_tv_above_floor_is_sure():
    items = [_item(description='Hisense 55A6N UHD 4K Smart TV 55 Pulgadas', unit_retail=489.0)]
    tvs = insights.find_tvs(items)
    assert tvs[0].confidence == "seguro"


def test_same_asin_price_disparity_flags_cheap_line():
    items = [
        _item(description="Robot aspirador X", asin="B0DUPE", unit_retail=300.0),
        _item(description="Robot aspirador X", asin="B0DUPE", unit_retail=12.0),
    ]
    found = insights.find_giveaways(items)
    assert len(found) == 1
    assert found[0].item.unit_retail == 12.0
    assert found[0].tier == "dudoso"


# --- Containers ---------------------------------------------------------

def test_sparse_box_flagged():
    items = []
    # Five normal boxes with 40 units each...
    for box in range(5):
        for n in range(40):
            items.append(_item(box_id=f"BOX{box}", description=f"item {box}-{n}"))
    # ...and one box with only 2 declared units.
    items += [_item(box_id="BOXSPARSE"), _item(box_id="BOXSPARSE")]

    boxes = insights.container_analysis(items, "box_id", "caja")
    flagged = [b for b in boxes if b.suspicious]
    assert [b.container_id for b in flagged] == ["BOXSPARSE"]


def test_single_container_never_flagged():
    items = [_item(box_id="ONLY"), _item(box_id="ONLY")]
    boxes = insights.container_analysis(items, "box_id", "caja")
    assert not any(b.suspicious for b in boxes)


# --- Breakdown / report -------------------------------------------------

def test_breakdown_by_department():
    items = [
        _item(department="Automotive", unit_retail=100.0),
        _item(department="Automotive", unit_retail=50.0),
        _item(department="Kitchen", unit_retail=25.0),
    ]
    groups = insights.breakdown(items, "department")
    assert groups[0].name == "Automotive"
    assert groups[0].units == 2
    assert groups[0].retail == 150.0
    assert groups[0].pct_retail == 85.7


def test_giveaway_value_estimation():
    items = [
        # iPhone declared at 10, typical 250 -> hidden 240 (sure)
        _item(description="Apple iPhone 16 128GB", unit_retail=10.0),
        # MacBook declared at 150, typical 600 -> hidden 450 (doubtful)
        _item(description="Apple MacBook Air M3", unit_retail=150.0, asin="B0MB"),
    ]
    result = insights.deep_analyze(items)
    assert result.giveaway_value_sure == 240.0
    assert result.giveaway_value_doubt == 450.0


def test_giveaway_evidence_in_report():
    items = [_item(description="Apple iPhone 16 128GB", unit_retail=10.0, asin="B0IPHONE16")]
    result = insights.deep_analyze(items, label="evidencia")
    report = insights.render_report(result)
    assert "Valor estimado regalado" in report
    assert "[B0IPHONE16](https://www.amazon.es/dp/B0IPHONE16)" in report
    assert "iPhone 16" in report


def test_render_report_smoke():
    items = [
        _item(description='Samsung TV 50" UHD', unit_retail=400.0),
        _item(description="Apple iPhone 16", unit_retail=10.0),
        _item(description="Sarten antiadherente", unit_retail=30.0),
    ]
    result = insights.deep_analyze(items, label="test-lot")
    report = insights.render_report(result)
    assert "test-lot" in report
    assert "Televisores" in report
    assert "regalados" in report.lower()
    assert "iPhone" in report
