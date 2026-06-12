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


def test_gaming_peripheral_naming_console_not_flagged():
    items = [
        _item(description="The G-Lab Korp Cobalt Auriculares Gaming PC PS5 Xbox",
              unit_retail=17.84, asin="B0GLAB1"),
        _item(description="Corsair Void v2 Wireless Auriculares para Juegos PS5",
              unit_retail=119.99, asin="B0CORS1"),
        _item(description="Mando inalámbrico compatible Nintendo Switch",
              unit_retail=15.0, asin="B0MANDO1"),
    ]
    assert insights.find_giveaways(items) == []


def test_real_console_at_absurd_price_still_flagged():
    items = [_item(description="Sony PlayStation 5 Slim 1TB Digital", unit_retail=20.0)]
    found = insights.find_giveaways(items)
    assert len(found) == 1
    assert found[0].tier == "seguro"


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


def test_tv_accessory_category_not_sure_tv():
    # An expensive wall mount categorized under "TV Mounts" must not count
    # as a TV panel even with no accessory word in the description.
    items = [
        _item(
            description="ECHOGEAR brazo articulado movimiento completo",
            category="Electronics",
            subcategory="TV Mounts & Stands",
            unit_retail=120.0,
        )
    ]
    tvs = insights.find_tvs(items)
    assert not any(t.confidence == "seguro" for t in tvs)


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

def _box_pallet(pallet_id: str, box_units: dict, weight: float = 1.0):
    """Build a pallet whose boxes carry the given units each."""
    items = []
    for box_id, units in box_units.items():
        for n in range(units):
            items.append(
                _item(
                    pallet_id=pallet_id,
                    box_id=box_id,
                    description=f"item {box_id}-{n}",
                    weight_kg=weight,
                )
            )
    return items


def test_sparse_box_in_box_pallet_flagged():
    # Six boxes: five with 40 units, one with only 2 -> only that one flags.
    units = {f"B{n}": 40 for n in range(5)}
    units["BSPARSE"] = 2
    boxes, pallets = insights.analyze_containers(_box_pallet("P1", units))
    flagged = [b for b in boxes if b.suspicious]
    assert [b.container_id for b in flagged] == ["BSPARSE"]
    assert "REGALADO" in flagged[0].reason
    # 6 of 6 boxes declared -> the pallet itself is fine.
    assert pallets[0].pallet_type == "cajas"
    assert pallets[0].suspicious is False


def test_box_pallet_with_missing_boxes_flagged_as_gifted():
    boxes, pallets = insights.analyze_containers(
        _box_pallet("P1", {"B1": 40, "B2": 38, "B3": 41})
    )
    assert pallets[0].pallet_type == "cajas"
    assert pallets[0].suspicious is True
    assert pallets[0].missing_boxes == 3
    assert "3 de 6" in pallets[0].reason
    assert "REGALADAS" in pallets[0].reason


def test_sparse_but_heavy_box_is_bulky_not_suspicious():
    # Six boxes; the sparse one carries 3 items of 12 kg each (36 kg total,
    # comparable to its 40 kg siblings): big objects fill it, not gifts.
    units = {f"B{n}": 40 for n in range(5)}
    items = _box_pallet("P1", units)  # 1 kg per item -> 40 kg per box
    for n in range(3):
        items.append(
            _item(pallet_id="P1", box_id="BHEAVY",
                  description=f"objeto voluminoso {n}", weight_kg=12.0)
        )
    boxes, _ = insights.analyze_containers(items)
    heavy = next(b for b in boxes if b.container_id == "BHEAVY")
    assert heavy.suspicious is False
    assert "voluminosos" in heavy.reason


def test_sparse_box_with_big_dimensions_in_name_not_suspicious():
    units = {f"B{n}": 40 for n in range(5)}
    items = _box_pallet("P1", units)
    for n in range(3):
        items.append(
            _item(pallet_id="P1", box_id="BDIM",
                  description=f"Mesa auxiliar madera 90x60x75 cm modelo {n}",
                  weight_kg=None)
        )
    boxes, _ = insights.analyze_containers(items)
    dim = next(b for b in boxes if b.container_id == "BDIM")
    assert dim.suspicious is False


def test_large_object_pallet_never_flagged_for_few_units():
    # Two treadmills of 60 kg on one pallet: completely normal.
    items = [
        _item(pallet_id="PXL", box_id="PKG1", description="Cinta de correr",
              weight_kg=60.0),
        _item(pallet_id="PXL", box_id="PKG1", description="Cinta de correr",
              weight_kg=62.0),
    ]
    boxes, pallets = insights.analyze_containers(items)
    assert pallets[0].pallet_type == "objetos grandes"
    assert pallets[0].suspicious is False
    assert boxes == []  # a single package is not a real box


def test_loose_medium_pallet_is_granel():
    items = [
        _item(pallet_id="PG", box_id="PKG1", description=f"silla {n}", weight_kg=6.0)
        for n in range(20)
    ]
    _, pallets = insights.analyze_containers(items)
    assert pallets[0].pallet_type == "granel"
    assert pallets[0].suspicious is False


def test_cheap_but_full_box_not_flagged():
    # A box full of cheap items is NOT suspicious: value is never a criterion.
    units = {f"B{n}": 40 for n in range(5)}
    items = _box_pallet("P1", units)
    items.append(_item(pallet_id="P1", box_id="BCHEAP", description="barato 0"))
    for n in range(39):
        items.append(
            _item(pallet_id="P1", box_id="BCHEAP", description=f"barato {n+1}",
                  unit_retail=0.5)
        )
    boxes, _ = insights.analyze_containers(items)
    cheap = next(b for b in boxes if b.container_id == "BCHEAP")
    assert cheap.suspicious is False


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
