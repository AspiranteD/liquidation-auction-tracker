"""Rule engine that decides whether an auction is worth an alert.

Alerts are reminders tied to the auction close (see pipeline.py): the first
one fires inside the 30-minute window before close, and a last-call one fires
inside the 5-minute window when the lot is still a very good deal. This module
only answers "does this auction qualify right now?" — the timing windows live
in the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .calculator import BidCalculator, CostBreakdown
from .config import AlertRules
from .models import Auction


def lot_family(lot_type: Optional[str]) -> Optional[str]:
    """Normalize a parsed lot type to its family.

    "4 Pallets De" / "4 Pallets" -> "4 Pallets"; "Small Truckload" and
    "Truckload" map to themselves. Anything else is unmonitored (None).
    """
    if not lot_type:
        return None
    normalized = lot_type.strip().lower()
    if normalized.startswith("small truckload"):
        return "Small Truckload"
    if normalized.startswith("truckload"):
        return "Truckload"
    if normalized.startswith("4 pallets"):
        return "4 Pallets"
    return None


def is_electronics(auction: Auction, rules: AlertRules) -> bool:
    """True when the lot title mentions an electronics category keyword."""
    title = (auction.title or "").lower()
    return any(keyword.lower() in title for keyword in rules.electronics_keywords)


@dataclass
class AlertDecision:
    is_key: bool
    reasons: List[str]
    breakdown: Optional[CostBreakdown] = None
    electronics: bool = False
    threshold_pct: float = 0.0
    current_total_pct: Optional[float] = None
    very_good: bool = False
    # ``static_ok``: passes the filters that DON'T change during the auction
    # (country, lot family, retail minimum, pieces). ``over_limit``: the
    # current bid already implies a landed cost above the ceiling. A key lot
    # is exactly static_ok AND not over_limit. Reports include static_ok
    # lots and drop over_limit ones, because at the start every auction sits
    # well under the ceiling (initial bids are low) and only the price near
    # close — 30 min out — separates the real buys.
    static_ok: bool = False
    over_limit: bool = False


def evaluate(
    auction: Auction, rules: AlertRules, calculator: BidCalculator
) -> AlertDecision:
    """Return whether ``auction`` matches the buying criteria right now.

    The ceiling applies to the TOTAL landed cost implied by the current bid
    (no bid yet counts as bid 0). The suggested max bid in ``breakdown`` is
    computed against the applicable ceiling (12% normal / 15% electronics).
    """
    # Static filters (country, lot family, retail minimum, pieces) don't
    # change during the auction; the price filter does. Keep them apart.
    static_reasons: List[str] = []

    family = lot_family(auction.lot_type)
    electronics = is_electronics(auction, rules)
    threshold = (
        rules.electronics_max_total_pct if electronics else rules.max_total_cost_pct
    )

    breakdown: Optional[CostBreakdown] = None
    if auction.retail_value and family:
        breakdown = calculator.max_bid_for_retail_pct(
            auction.retail_value, threshold, auction.lot_type
        )

    if rules.countries and auction.country not in rules.countries:
        static_reasons.append(f"country {auction.country} not in monitor list")

    if family is None:
        static_reasons.append(f"lot type {auction.lot_type!r} not monitored")

    if auction.retail_value is None:
        static_reasons.append("retail value unknown")
    elif family is not None:
        min_retail = rules.min_retail_by_type.get(family)
        if min_retail is None:
            static_reasons.append(f"no retail minimum configured for {family}")
        elif auction.retail_value < min_retail:
            static_reasons.append(
                f"retail {auction.retail_value:,.0f} below min "
                f"{min_retail:,.0f} for {family}"
            )

    if auction.pieces is not None and auction.pieces < rules.min_pieces:
        static_reasons.append(f"pieces {auction.pieces} below min {rules.min_pieces}")

    # Total landed cost implied by the current bid (the price filter).
    current_total_pct: Optional[float] = None
    price_reason: Optional[str] = None
    if auction.retail_value and family:
        current = calculator.cost_breakdown_for_bid(
            auction.current_bid or 0.0,
            auction.lot_type,
            retail_value=auction.retail_value,
        )
        current_total_pct = current.total_pct_of_retail
        if current_total_pct is not None and current_total_pct > threshold:
            price_reason = (
                f"current bid implies {current_total_pct:.1%} of retail "
                f"(> {threshold:.0%}{' electronics' if electronics else ''})"
            )

    static_ok = not static_reasons
    over_limit = price_reason is not None
    reasons = static_reasons + ([price_reason] if price_reason else [])
    is_key = static_ok and not over_limit
    very_good = bool(
        is_key
        and current_total_pct is not None
        and current_total_pct <= rules.very_good_total_pct
    )
    return AlertDecision(
        is_key=is_key,
        reasons=reasons,
        breakdown=breakdown,
        electronics=electronics,
        threshold_pct=threshold,
        current_total_pct=current_total_pct,
        very_good=very_good,
        static_ok=static_ok,
        over_limit=over_limit,
    )
