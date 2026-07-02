"""Rank active lots by manifest-weighted expected value.

The alert engine (alerts.py) estimates a lot's recovery from its TITLE categories
alone — good enough to gate an alert before the manifest is downloaded. Once the
manifest IS available, we can do much better: weight the recovery by each
department's real retail in the manifest (recovery.py::blended) AND add the
hidden value that only the manifest reveals — mislabeled premium ("regalados")
and undeclared boxes (insights.deep_analyze).

This module turns those existing pieces into one comparable score per lot so the
buyer can open only the 3-4 lots that actually matter each day.

    score = expected_revenue (recovery x effective retail) + hidden value
    hidden value = confirmed giveaway uplift + estimated undeclared boxes

Score is an optimistic upper bound (the historical recovery already averages in
past gift luck, so the manifest-visible hidden value partly double-counts), but
it is the right *ordering* signal: lots with more visible upside rank higher.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from .alerts import lot_family
from .calculator import BidCalculator
from .insights import ManifestInsights
from .models import Auction
from .recovery import DEFAULT_MULTIPLE, RecoveryModel


@dataclass
class LotRanking:
    auction_id: int
    title: str
    country: Optional[str]
    lot_type: Optional[str]
    retail: float
    effective_retail: float       # retail minus TV loss (recovery base)
    recovery: float               # manifest-weighted recovery fraction
    coverage: float               # share of retail matched to real category data
    expected_revenue: float       # recovery * effective_retail
    giveaway_sure: float          # confirmed "regalados" uplift
    gifted_boxes: float           # estimated value of undeclared boxes
    hidden_value: float           # giveaway_sure + gifted_boxes
    score: float                  # expected_revenue + hidden_value
    recommended_bid: float        # recovery-based max bid (landed = revenue/multiple)
    current_bid: float
    headroom: float               # recommended_bid - current_bid (>0 = still buyable)
    url: str

    def as_row(self) -> Dict:
        return asdict(self)


def rank_lot(
    auction: Auction,
    insights: ManifestInsights,
    model: RecoveryModel,
    calculator: BidCalculator,
    multiple: float = DEFAULT_MULTIPLE,
) -> LotRanking:
    """Score one lot from its auction meta + deep-analyzed manifest."""
    # Recovery base: effective retail (TVs recover ~0, so exclude their retail).
    base_retail = insights.effective_retail or insights.total_retail
    retail = auction.retail_value or insights.total_retail

    blend = model.blended(insights.by_department)
    family = lot_family(auction.lot_type)
    rec = model.recommend_bid(
        base_retail, blend.recovery, calculator, family,
        country=auction.country, multiple=multiple, coverage=blend.coverage,
    )

    expected_revenue = base_retail * blend.recovery
    hidden = insights.giveaway_value_sure + insights.gifted_box_value_point
    current = auction.current_bid or 0.0
    return LotRanking(
        auction_id=auction.auction_id,
        title=(auction.title or "")[:80],
        country=auction.country,
        lot_type=auction.lot_type,
        retail=round(retail, 2),
        effective_retail=round(base_retail, 2),
        recovery=round(blend.recovery, 4),
        coverage=round(blend.coverage, 3),
        expected_revenue=round(expected_revenue, 2),
        giveaway_sure=round(insights.giveaway_value_sure, 2),
        gifted_boxes=round(insights.gifted_box_value_point, 2),
        hidden_value=round(hidden, 2),
        score=round(expected_revenue + hidden, 2),
        recommended_bid=round(rec.bid, 2),
        current_bid=round(current, 2),
        headroom=round(rec.bid - current, 2),
        url=auction.url or "",
    )


def rank_lots(rankings: List[LotRanking]) -> List[LotRanking]:
    """Sort by score (most total expected value first)."""
    return sorted(rankings, key=lambda r: r.score, reverse=True)


_COLS = [
    ("#", "auction_id", 7), ("tipo", "lot_type", 16), ("retail", "retail", 10),
    ("recup%", "recovery", 7), ("cob%", "coverage", 6),
    ("ing_esp", "expected_revenue", 9), ("regalad", "giveaway_sure", 8),
    ("cajas_oc", "gifted_boxes", 9), ("SCORE", "score", 10),
    ("puja_max", "recommended_bid", 9), ("puja_act", "current_bid", 9),
    ("margen", "headroom", 9),
]


def render_table(rankings: List[LotRanking], limit: int = 0) -> str:
    """Plain-text ranked table (also fine to drop into a markdown code block)."""
    rows = rank_lots(rankings)
    if limit:
        rows = rows[:limit]
    header = "  ".join(f"{name:>{w}}" for name, _, w in _COLS)
    lines = [header, "-" * len(header)]
    for r in rows:
        cells = []
        for _name, attr, w in _COLS:
            v = getattr(r, attr)
            if attr == "recovery" or attr == "coverage":
                cells.append(f"{v*100:>{w}.0f}")
            elif isinstance(v, float):
                cells.append(f"{v:>{w},.0f}")
            else:
                cells.append(f"{str(v):>{w}}")
        lines.append("  ".join(cells))
    return "\n".join(lines)


def to_csv_rows(rankings: List[LotRanking]) -> List[Dict]:
    return [r.as_row() for r in rank_lots(rankings)]
