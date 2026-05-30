"""Bid calculator for liquidation truckloads.

When you buy a liquidation truckload at auction, the headline bid is only part
of the real cost. The total landed cost also includes transport, VAT, the
marketplace fee and (for Spanish resellers) the "recargo de equivalencia" (RE)
surcharge.

This module answers two questions:

1. ``max_bid_for_target_cost`` — "If I want my total landed cost to be X% of the
   retail value, what is the maximum bid I can place?" (the reverse calculation
   you normally do *before* bidding).
2. ``cost_breakdown_for_bid`` — "If I place this bid, what is my real total
   cost?" (the forward calculation, useful after the auction closes).

Cost model
----------
    total_cost = bid + transport + vat + bstock_fee + re
    vat        = (transport + bid) * VAT_RATE
    bstock_fee = bid * BSTOCK_FEE_RATE
    re         = total_cost * RE_RATE

Solving the circular ``re`` dependency gives the two closed-form functions
below. Both directions are consistent: feeding the output of one into the other
returns the original input.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Optional

VAT_RATE = 0.21          # IVA
BSTOCK_FEE_RATE = 0.04   # B-Stock buyer fee
RE_RATE = 0.052          # Recargo de equivalencia (Spanish resale surcharge)

# Flat transport cost per lot type (EUR). These are generic logistics rates;
# adjust them to your own carrier quotes via BidCalculator(transport_costs=...).
DEFAULT_TRANSPORT_COSTS: Dict[str, float] = {
    "Truckload": 636.12,
    "Small Truckload": 433.11,
    "4 Pallets DE": 790.0,
    "4 Pallets PL": 900.0,
    "4 Pallets IT": 750.0,
    "4 Pallets": 318.99,
}


@dataclass
class CostBreakdown:
    """Full landed-cost breakdown for a given bid."""

    bid: float
    transport: float
    vat: float
    bstock_fee: float
    re: float
    total_cost: float
    retail_value: Optional[float] = None

    @property
    def bid_pct_of_retail(self) -> Optional[float]:
        """Bid as a fraction of retail value (the spreadsheet's 'Porcentaje puja')."""
        if not self.retail_value:
            return None
        return self.bid / self.retail_value

    @property
    def total_pct_of_retail(self) -> Optional[float]:
        """Total landed cost as a fraction of retail value (the spreadsheet's '% Total')."""
        if not self.retail_value:
            return None
        return self.total_cost / self.retail_value

    def as_dict(self) -> Dict[str, Optional[float]]:
        data = asdict(self)
        data["bid_pct_of_retail"] = self.bid_pct_of_retail
        data["total_pct_of_retail"] = self.total_pct_of_retail
        return data


class BidCalculator:
    """Compute maximum bids and landed costs for liquidation lots."""

    def __init__(
        self,
        transport_costs: Optional[Dict[str, float]] = None,
        vat_rate: float = VAT_RATE,
        bstock_fee_rate: float = BSTOCK_FEE_RATE,
        re_rate: float = RE_RATE,
    ) -> None:
        self.transport_costs = dict(transport_costs or DEFAULT_TRANSPORT_COSTS)
        self.vat_rate = vat_rate
        self.bstock_fee_rate = bstock_fee_rate
        self.re_rate = re_rate

    def transport_for(self, lot_type: Optional[str]) -> float:
        """Look up the flat transport cost for a lot type (case-insensitive).

        Unknown or missing types return 0.0, matching the spreadsheet's IFERROR
        fallback.
        """
        if not lot_type:
            return 0.0
        normalized = lot_type.strip().lower()
        for key, value in self.transport_costs.items():
            if key.lower() == normalized:
                return value
        return 0.0

    def max_bid_for_target_cost(self, total_cost: float, transport: float) -> float:
        """Maximum bid so that the landed cost equals ``total_cost``.

        Mirrors the spreadsheet formula::

            (total_cost - transport*(1+VAT) - RE*total_cost) / (1 + fee + VAT)
        """
        numerator = total_cost - transport * (1 + self.vat_rate) - self.re_rate * total_cost
        denominator = 1 + self.bstock_fee_rate + self.vat_rate
        bid = numerator / denominator
        return max(bid, 0.0)

    def max_bid_for_retail_pct(
        self, retail_value: float, target_pct: float, lot_type: Optional[str]
    ) -> CostBreakdown:
        """Maximum bid so that landed cost is ``target_pct`` of ``retail_value``.

        ``target_pct`` is a fraction, e.g. 0.30 for 30%.
        """
        transport = self.transport_for(lot_type)
        total_cost = retail_value * target_pct
        bid = self.max_bid_for_target_cost(total_cost, transport)
        return self.cost_breakdown_for_bid(bid, lot_type, retail_value=retail_value)

    def cost_breakdown_for_bid(
        self,
        bid: float,
        lot_type: Optional[str],
        retail_value: Optional[float] = None,
        transport: Optional[float] = None,
    ) -> CostBreakdown:
        """Full landed-cost breakdown for a given ``bid``.

        Resolves the circular RE dependency::

            total_cost = (bid + transport + vat + fee) / (1 - RE_RATE)
        """
        if transport is None:
            transport = self.transport_for(lot_type)
        vat = (transport + bid) * self.vat_rate
        bstock_fee = bid * self.bstock_fee_rate
        base = bid + transport + vat + bstock_fee
        total_cost = base / (1 - self.re_rate)
        re = total_cost * self.re_rate
        return CostBreakdown(
            bid=round(bid, 2),
            transport=round(transport, 2),
            vat=round(vat, 2),
            bstock_fee=round(bstock_fee, 2),
            re=round(re, 2),
            total_cost=round(total_cost, 2),
            retail_value=retail_value,
        )
