from liquidation_tracker import parser


def test_lot_type_truckload():
    assert parser.parse_lot_type("Truckload of Floorcare, Laundry & More") == "Truckload"


def test_lot_type_small_truckload():
    title = "Small Truckload of Kitchen, Home Goods & More"
    assert parser.parse_lot_type(title) == "Small Truckload"


def test_lot_type_4_pallets_does_not_swallow_of():
    # Regression: "of" must not be parsed as a country suffix ("4 Pallets Of"),
    # which produced an unknown lot type with transport cost 0.
    title = "4 Pallets of Auto Goods, Home Improvement & More, 611 Pieces"
    assert parser.parse_lot_type(title) == "4 Pallets"


def test_lot_type_4_pallets_with_country_suffix():
    assert parser.parse_lot_type("4 Pallets DE of Sporting Goods") == "4 Pallets De"


def test_retail_value():
    title = "..., Customer Returns, Total Retail €16,404, ES Stock"
    assert parser.parse_retail_value(title) == 16404.0


def test_country():
    title = "..., Customer Returns, Total Retail €16,404, ES Stock"
    assert parser.parse_country(title) == "ES"
