"""Rule engine that decides whether an auction is worth an email alert."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .calculator import BidCalculator, CostBreakdown
from .config import AlertRules
from .models import Auction


@dataclass
class AlertDecision:
    is_key: bool
    reasons: List[str]
    breakdown: Optional[CostBreakdown] = None


def evaluate(
    auction: Auction, rules: AlertRules, calculator: BidCalculator
) -> AlertDecision:
    """Return whether ``auction`` matches the buying criteria, with the
    suggested bid breakdown attached."""
    reasons: List[str] = []

    breakdown: Optional[CostBreakdown] = None
    if auction.retail_value and auction.lot_type is not None:
        breakdown = calculator.max_bid_for_retail_pct(
            auction.retail_value, rules.target_total_pct, auction.lot_type
        )

    if rules.countries and auction.country not in rules.countries:
        reasons.append(f"country {auction.country} not in monitor list")

    if auction.retail_value is None or auction.retail_value < rules.min_retail_value:
        reasons.append(
            f"retail {auction.retail_value} below min {rules.min_retail_value}"
        )

    if auction.pieces is not None and auction.pieces < rules.min_pieces:
        reasons.append(f"pieces {auction.pieces} below min {rules.min_pieces}")

    # If the *current* bid already implies a landed cost above your ceiling,
    # the lot is no longer attractive.
    if auction.current_bid and breakdown is not None:
        current_total = calculator.cost_breakdown_for_bid(
            auction.current_bid, auction.lot_type, retail_value=auction.retail_value
        )
        if (
            current_total.total_pct_of_retail
            and current_total.total_pct_of_retail > rules.max_total_cost_pct
        ):
            reasons.append(
                f"current bid implies {current_total.total_pct_of_retail:.0%} of "
                f"retail (> {rules.max_total_cost_pct:.0%})"
            )

    is_key = not reasons
    return AlertDecision(is_key=is_key, reasons=reasons, breakdown=breakdown)
