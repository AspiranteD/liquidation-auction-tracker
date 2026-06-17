"""Rule engine that decides whether an auction is worth an alert.

Alerts are reminders tied to the auction close (see pipeline.py): the first
one fires inside the 30-minute window before close, and a last-call one fires
inside the 5-minute window when the lot is still a very good deal. This module
only answers "does this auction qualify right now?" — the timing windows live
in the pipeline.

The price decision is recovery-based (no fixed 12%/15% rule): the recommended
max bid makes the landed cost equal ``recovery / bid_multiple`` of retail, where
``recovery`` comes from the macro sales study (recovery.py), estimated from the
auction's title categories. A lot is over the limit when its current bid exceeds
that recommended bid.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .calculator import BidCalculator, CostBreakdown
from .config import AlertRules
from .models import Auction
from .recovery import RecoveryModel, load_recovery, transport_key


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


# "4 Pallets of PC Goods, Electronics & More, 928 Pieces, ..." -> the category
# phrase sits between "of " and the piece count / "& More".
_TITLE_CATEGORIES_RE = re.compile(
    r"\bof\s+(.+?)(?:\s*&\s*more)?,\s*[\d,]+\s*pieces", re.IGNORECASE
)


def lot_categories(title: Optional[str]) -> List[str]:
    """Extract the category tokens from a B-Stock auction title."""
    if not title:
        return []
    match = _TITLE_CATEGORIES_RE.search(title)
    phrase = match.group(1) if match else ""
    if not phrase:
        # Fallback: take whatever follows "of " up to the first comma.
        alt = re.search(r"\bof\s+(.+)", title, re.IGNORECASE)
        phrase = alt.group(1).split(",")[0] if alt else ""
    tokens = re.split(r",|&", phrase)
    return [t.strip() for t in tokens if t.strip() and t.strip().lower() != "more"]


def is_electronics(auction: Auction, rules: AlertRules) -> bool:
    """True when the lot title mentions an electronics category keyword
    (informational only; the bid no longer depends on it)."""
    title = (auction.title or "").lower()
    return any(keyword.lower() in title for keyword in rules.electronics_keywords)


@dataclass
class AlertDecision:
    is_key: bool
    reasons: List[str]
    breakdown: Optional[CostBreakdown] = None   # recommended max-bid breakdown
    electronics: bool = False
    recovery: float = 0.0               # estimated recovery fraction used
    coverage: float = 0.0               # share of categories matched to real data
    recommended_bid: float = 0.0        # recovery/multiple max bid
    current_bid_pct: Optional[float] = None      # current bid landed cost / retail
    headroom: Optional[float] = None    # recommended_bid - current_bid
    very_good: bool = False
    # ``static_ok``: passes the filters that DON'T change during the auction
    # (country, lot family, retail minimum, pieces). ``over_limit``: the
    # current bid already exceeds the recommended (recovery-based) bid. A key
    # lot is exactly static_ok AND not over_limit.
    static_ok: bool = False
    over_limit: bool = False


def evaluate(
    auction: Auction,
    rules: AlertRules,
    calculator: BidCalculator,
    recovery_model: Optional[RecoveryModel] = None,
) -> AlertDecision:
    """Return whether ``auction`` matches the buying criteria right now.

    The recommended max bid is derived from the lot's estimated recovery
    (recovery / ``rules.bid_multiple`` of retail). ``over_limit`` means the
    current bid already exceeds that recommended bid.
    """
    if recovery_model is None:
        recovery_model = load_recovery()

    static_reasons: List[str] = []

    family = lot_family(auction.lot_type)
    electronics = is_electronics(auction, rules)

    # Estimate recovery from the title categories.
    blend = recovery_model.estimate_from_tokens(lot_categories(auction.title))
    recovery, coverage = blend.recovery, blend.coverage

    breakdown: Optional[CostBreakdown] = None
    recommended_bid = 0.0
    if auction.retail_value and family:
        rec = recovery_model.recommend_bid(
            auction.retail_value, recovery, calculator, family,
            country=auction.country, multiple=rules.bid_multiple, coverage=coverage,
        )
        breakdown = rec.breakdown
        recommended_bid = rec.bid

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

    # Price filter: current bid vs the recovery-based recommended bid.
    current_bid = auction.current_bid or 0.0
    current_bid_pct: Optional[float] = None
    headroom: Optional[float] = None
    price_reason: Optional[str] = None
    if auction.retail_value and family:
        current = calculator.cost_breakdown_for_bid(
            current_bid, auction.lot_type, retail_value=auction.retail_value
        )
        current_bid_pct = current.total_pct_of_retail
        headroom = recommended_bid - current_bid
        if current_bid > recommended_bid:
            price_reason = (
                f"current bid {current_bid:,.0f} implies a landed cost above the "
                f"recommended max {recommended_bid:,.0f} "
                f"(recovery {recovery:.0%}/{rules.bid_multiple:g})"
            )

    static_ok = not static_reasons
    over_limit = price_reason is not None
    reasons = static_reasons + ([price_reason] if price_reason else [])
    is_key = static_ok and not over_limit
    very_good = bool(
        is_key
        and recommended_bid > 0
        and current_bid <= recommended_bid * rules.very_good_headroom_fraction
    )
    return AlertDecision(
        is_key=is_key,
        reasons=reasons,
        breakdown=breakdown,
        electronics=electronics,
        recovery=recovery,
        coverage=coverage,
        recommended_bid=recommended_bid,
        current_bid_pct=current_bid_pct,
        headroom=headroom,
        very_good=very_good,
        static_ok=static_ok,
        over_limit=over_limit,
    )
