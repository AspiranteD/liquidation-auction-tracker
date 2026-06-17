"""Recovery-based bid model (the "macro estudio" of Reusalia).

Instead of a flat rule (12%/15% of retail), the recommended bid is derived from
how much each department/category *actually recovers* in our own sales history:

    recovery = real revenue / B-Stock retail

A truck's blended recovery is the retail-weighted average of its departments'
recoveries. The recommended bid targets a landed cost of ``recovery / multiple``
of retail — i.e. buying at a 1/``multiple`` of expected revenue (a ``multiple``×
gross markup). Default multiple is 3 (the "múltiplo de caja" used historically).

The recovery table is precomputed offline by scripts/build_recovery.py into
data/recovery.json, so the monitor never needs the backend or the DB at runtime.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from .calculator import BidCalculator, CostBreakdown

logger = logging.getLogger(__name__)

RECOVERY_PATH = "data/recovery.json"
DEFAULT_MULTIPLE = 3.0
# Fallback when the JSON is missing entirely (mean real recovery per truck ~24%).
_FALLBACK_GLOBAL = 0.24

# B-Stock title category tokens -> the name used in our sales study. Ported
# from scripts/recomendador_camiones.py so the alert engine can estimate a
# lot's recovery from its TITLE alone (before the manifest is downloaded).
TITLE_ALIAS: Dict[str, str] = {
    "hot beverage makers": "Hot Beverage Makers", "floorcare": "Floorcare",
    "housewares": "Housewares", "office supplies": "Office Supplies",
    "office products": "Office Supplies", "printing hardware": "Printing Hardware",
    "power tools": "Power Tools", "headphones": "Headphones",
    "wireless": "Wireless", "camera": "Camera", "games": "Games & Puzzles",
    "lighting": "Lighting", "cookware": "Cookware", "kitchen": "Kitchen",
    "car seats": "Car Seats & Accessories", "furniture": "Furniture",
    "bedding": "Bedding", "toys": "Toys",
    "personal care": "Shaving & Hair Removal Appliances",
    "beauty": "Beauty", "auto goods": "Spare & Repair Parts Car & Truck",
    "sporting goods": "Exercise & Fitness", "home improvement": "Home Improvement",
    "lawn and garden": "Lawn and Garden", "pet products": "Pet Products",
    "pc goods": "PC", "electronics": "Electronics",
    "home entertainment": "Home Entertainment", "video games": "Video Games",
    "drugstore": "Drugstore",
}


@dataclass
class BidRecommendation:
    """A recovery-based bid suggestion for one retail base."""

    retail: float
    recovery: float          # blended recovery fraction used
    multiple: float          # target gross markup (landed = recovery/multiple)
    breakdown: CostBreakdown  # the bid + full landed-cost breakdown
    coverage: float          # share of retail matched to real data (0..1)

    @property
    def bid(self) -> float:
        return self.breakdown.bid

    @property
    def expected_revenue(self) -> float:
        return self.retail * self.recovery


class RecoveryModel:
    """Per-department / per-category recovery, with global fallback."""

    def __init__(
        self,
        global_recovery: float,
        by_department: Dict[str, dict],
        by_category: Dict[str, dict],
        min_sample: int = 30,
    ) -> None:
        self.global_recovery = global_recovery
        self.by_department = by_department
        self.by_category = by_category
        self.min_sample = min_sample

    # -- lookups -----------------------------------------------------------

    def _reliable(self, table: Dict[str, dict], name: Optional[str]) -> Optional[float]:
        if not name:
            return None
        entry = table.get(name.strip())
        if entry and entry.get("n", 0) >= self.min_sample:
            return float(entry["recovery"])
        return None

    def for_group(self, department: Optional[str], category: Optional[str]) -> Optional[float]:
        """Recovery for a group: department first (broad, well-sampled), then
        category, else None (caller blends in global)."""
        return (
            self._reliable(self.by_department, department)
            or self._reliable(self.by_category, category)
        )

    def _lookup_token(self, token: str) -> Optional[float]:
        """Resolve a free-text token (manifest group name or B-Stock title
        category) to a recovery, trying department, category, then alias."""
        t = token.strip()
        if not t:
            return None
        rec = self._reliable(self.by_department, t) or self._reliable(self.by_category, t)
        if rec is not None:
            return rec
        alias = TITLE_ALIAS.get(t.lower())
        if alias:
            return self._reliable(self.by_department, alias) or self._reliable(
                self.by_category, alias
            )
        return None

    def estimate_from_tokens(self, tokens: List[str]) -> "BlendResult":
        """Estimate recovery from category tokens of equal weight (used by the
        alert engine, which only has the auction title). Unmatched tokens fall
        back to the global recovery; coverage is the matched share."""
        if not tokens:
            return BlendResult(self.global_recovery, 0.0)
        recs, matched = [], 0
        for token in tokens:
            rec = self._lookup_token(token)
            if rec is None:
                recs.append(self.global_recovery)
            else:
                recs.append(rec)
                matched += 1
        return BlendResult(sum(recs) / len(recs), matched / len(tokens))

    # -- blending ----------------------------------------------------------

    def blended(self, groups: List["GroupAggregate"]) -> "BlendResult":
        """Retail-weighted recovery across a lot's departments.

        ``groups`` is any sequence of objects exposing ``name`` and ``retail``
        (insights.GroupStats fits). Unmatched retail falls back to the global
        recovery, and the matched share is reported as ``coverage``.
        """
        total_retail = sum(max(g.retail, 0.0) for g in groups) or 0.0
        if total_retail <= 0:
            return BlendResult(self.global_recovery, 0.0)

        matched_retail = 0.0
        weighted = 0.0
        for g in groups:
            retail = max(g.retail, 0.0)
            if retail <= 0:
                continue
            rec = self._reliable(self.by_department, g.name)
            if rec is None:
                rec = self._reliable(self.by_category, g.name)
            if rec is None:
                weighted += retail * self.global_recovery
            else:
                weighted += retail * rec
                matched_retail += retail
        recovery = weighted / total_retail
        return BlendResult(recovery, matched_retail / total_retail)

    # -- recommendation ----------------------------------------------------

    def recommend_bid(
        self,
        retail: float,
        recovery: float,
        calculator: BidCalculator,
        lot_type: Optional[str],
        country: Optional[str] = None,
        multiple: float = DEFAULT_MULTIPLE,
        coverage: float = 1.0,
    ) -> BidRecommendation:
        """Bid so landed cost = ``recovery / multiple`` of ``retail``."""
        target_pct = recovery / multiple if multiple else recovery
        lot_key = transport_key(lot_type, country)
        breakdown = calculator.max_bid_for_retail_pct(retail, target_pct, lot_key)
        return BidRecommendation(
            retail=retail,
            recovery=recovery,
            multiple=multiple,
            breakdown=breakdown,
            coverage=coverage,
        )


@dataclass
class BlendResult:
    recovery: float
    coverage: float


@dataclass
class GroupAggregate:
    """Minimal shape blended() needs (insights.GroupStats also satisfies it)."""

    name: str
    retail: float


# Countries with a specific "4 Pallets <C>" transport rate in the calculator's
# table; everything else (ES, FR, UK...) uses the base "4 Pallets" rate.
_PALLET_TRANSPORT_COUNTRIES = ("DE", "PL", "IT")


def transport_key(lot_type: Optional[str], country: Optional[str]) -> str:
    """Transport for "4 Pallets" depends on the destination country; truckloads
    don't. Only DE/PL/IT have a specific rate — ES/FR/UK fall back to the base
    "4 Pallets" key (otherwise the lookup misses and transport is treated as 0)."""
    if lot_type == "4 Pallets" and country in _PALLET_TRANSPORT_COUNTRIES:
        return f"4 Pallets {country}"
    return lot_type or ""


def load_recovery(path: str = RECOVERY_PATH) -> RecoveryModel:
    """Load data/recovery.json, or a global-only fallback model if absent."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        logger.warning(
            "recovery.json no disponible (%s); usando recuperación global %.0f%%. "
            "Ejecuta scripts/build_recovery.py para el estudio real.",
            path, _FALLBACK_GLOBAL * 100,
        )
        return RecoveryModel(_FALLBACK_GLOBAL, {}, {})
    return RecoveryModel(
        global_recovery=float(data.get("global", _FALLBACK_GLOBAL)),
        by_department=data.get("by_department", {}),
        by_category=data.get("by_category", {}),
        min_sample=int(data.get("min_sample", 30)),
    )
