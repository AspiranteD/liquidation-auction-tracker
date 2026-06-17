"""Price resolution + verified-giveaway behavior, all offline (fake resolver)."""
import json

from liquidation_tracker import insights
from liquidation_tracker.models import ManifestItem
from liquidation_tracker.pricing import PriceResolver, ResolvedPrice, prime_cache


class FakeResolver:
    """Stand-in PriceResolver: returns canned prices by ASIN (no DB/network)."""

    def __init__(self, prices: dict):
        self.prices = prices

    def resolve(self, asin):
        if asin in self.prices:
            return ResolvedPrice(asin, self.prices[asin], "fake", "alta")
        return ResolvedPrice(asin or "", None, "none", "baja")


def _item(**o) -> ManifestItem:
    base = dict(description="x", category="Home", subcategory="Misc",
                department="Home", qty=1, unit_retail=10.0, asin="B0X")
    base.update(o)
    return ManifestItem(**base)


def test_verified_giveaway_confirmed():
    # MacBook declared at 16, verified real price 1046 -> confirmed giveaway.
    items = [_item(description="Apple MacBook Air M4", unit_retail=16.0, asin="B0MB")]
    found = insights.find_giveaways(items, resolver=FakeResolver({"B0MB": 1046.0}))
    assert len(found) == 1
    assert found[0].tier == "seguro"
    assert found[0].verified is True
    assert found[0].reference_price == 1046.0
    assert abs(found[0].hidden_value - (1046.0 - 16.0)) < 0.01


def test_verified_false_positive_discarded():
    # Fotasy lens declared at 38.6, verified real price ~42 -> NOT a giveaway.
    items = [_item(description="Fotasy Objetivo 35 mm F1.6", unit_retail=38.6,
                   asin="B0LENS")]
    found = insights.find_giveaways(items, resolver=FakeResolver({"B0LENS": 42.0}))
    assert found == []


def test_apple_watch_band_false_positive_discarded():
    # Genuine Ocean Band: declared 49, real 99 -> 49 is 49% of 99, not a gift.
    items = [_item(description="Apple Watch Ocean Band 49 mm Navy",
                   unit_retail=49.0, asin="B0BAND")]
    found = insights.find_giveaways(items, resolver=FakeResolver({"B0BAND": 99.0}))
    assert found == []


def test_unresolved_extreme_still_sure():
    # No price found, but 8 / 250 typical = 3% -> extreme discount, kept sure.
    items = [_item(description="Samsung Galaxy S24 Ultra", unit_retail=8.0,
                   asin="B0NONE")]
    found = insights.find_giveaways(items, resolver=FakeResolver({}))
    assert len(found) == 1
    assert found[0].tier == "seguro"
    assert found[0].verified is False


def test_resolver_prefers_cache(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text('{"B0AAA": {"price": 123.0, "source": "db_scraped"}}',
                     encoding="utf-8")
    resolver = PriceResolver(cache_path=str(cache), use_db=False,
                             enable_scrape=False)
    result = resolver.resolve("B0AAA")
    assert result.price == 123.0
    assert result.source == "cache"


def test_resolver_missing_returns_none(tmp_path):
    resolver = PriceResolver(cache_path=str(tmp_path / "c.json"), use_db=False,
                             enable_scrape=False)
    result = resolver.resolve("B0MISSING")
    assert result.found is False
    assert result.price is None


def test_prime_cache_then_resolve_from_it(tmp_path):
    # "Normal web search" bridge: hand-verified prices land in the shared cache
    # and the next resolver reads them for free.
    cache = str(tmp_path / "cache.json")
    written = prime_cache({"B0AAA": 1046.0, "B0BBB": 250.0}, path=cache,
                          source="web")
    assert written == 2
    data = json.loads(open(cache, encoding="utf-8").read())
    assert data["B0AAA"]["price"] == 1046.0
    assert data["B0AAA"]["source"] == "web"

    resolver = PriceResolver(cache_path=cache, use_db=False, enable_scrape=False)
    assert resolver.resolve("B0AAA").price == 1046.0


def test_max_verify_caps_resolution_to_top_value(tmp_path):
    # Only the single highest-value suspect is resolved; the cheaper one is not
    # sent to the resolver (it falls back to the heuristic).
    asked = []

    class Counting:
        def resolve(self, asin):
            asked.append(asin)
            return ResolvedPrice(asin, 1046.0, "fake", "alta")

    items = [
        _item(description="Apple MacBook Air M4", unit_retail=16.0, asin="B0BIG"),
        _item(description="Apple AirPods", unit_retail=5.0, asin="B0SMALL"),
    ]
    insights.find_giveaways(items, resolver=Counting(), max_verify=1)
    # MacBook (typical 600) outranks AirPods (typical 100) for the budget.
    assert asked == ["B0BIG"]
