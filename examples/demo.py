"""Offline demo: shows the bid calculator and manifest analyzer end-to-end
without hitting the live B-Stock site.

    python examples/demo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from liquidation_tracker import analyzer
from liquidation_tracker.calculator import BidCalculator

SAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "sample_manifest.csv",
)


def demo_calculator():
    print("=" * 60)
    print("BID CALCULATOR")
    print("=" * 60)
    calc = BidCalculator()
    retail = 16670.0
    for pct in (0.20, 0.25, 0.30):
        b = calc.max_bid_for_retail_pct(retail, pct, "Small Truckload")
        print(
            f"Retail EUR {retail:,.0f} | target {pct:.0%} landed -> "
            f"max bid EUR {b.bid:,.2f} (total EUR {b.total_cost:,.2f})"
        )


def demo_analyzer():
    print("\n" + "=" * 60)
    print("MANIFEST ANALYSIS")
    print("=" * 60)
    items = analyzer.parse_manifest(SAMPLE)
    stats = analyzer.analyze(items)
    print(f"Items: {stats.total_items} | Units: {stats.total_units} | "
          f"Total retail: EUR {stats.total_retail:,.2f}")
    print("Top categories:")
    for cat, value in list(stats.categories.items())[:5]:
        print(f"  {cat:<25} EUR {value:,.2f}")


if __name__ == "__main__":
    demo_calculator()
    demo_analyzer()
