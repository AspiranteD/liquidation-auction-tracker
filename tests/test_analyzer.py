"""Tests for the manifest analyzer against the bundled sample CSV."""
import os

from liquidation_tracker import analyzer

SAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "sample_manifest.csv"
)


def test_parse_sample_manifest():
    items = analyzer.parse_manifest(SAMPLE)
    assert len(items) == 15
    # Weight should be converted from grams to kg.
    first = items[0]
    assert first.weight_kg == 2.4


def test_analyze_totals():
    items = analyzer.parse_manifest(SAMPLE)
    stats = analyzer.analyze(items)
    assert stats.total_items == 15
    assert stats.total_units > stats.total_items  # some lines have qty > 1
    assert stats.total_retail > 0
    assert "Electronics" in stats.categories
    assert len(stats.top_items) <= 10
