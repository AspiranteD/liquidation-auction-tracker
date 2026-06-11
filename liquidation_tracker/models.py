"""Plain data models shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class Auction:
    """A single liquidation auction as listed on B-Stock."""

    auction_id: int
    title: str
    url: str
    country: Optional[str] = None
    lot_type: Optional[str] = None
    retail_value: Optional[float] = None
    pieces: Optional[int] = None
    current_bid: Optional[float] = None
    end_time: Optional[datetime] = None
    lot_id: Optional[str] = None  # B-Stock SKU used to download the manifest
    scraped_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def as_dict(self) -> Dict:
        data = asdict(self)
        if self.end_time:
            data["end_time"] = self.end_time.isoformat()
        data["scraped_at"] = self.scraped_at.isoformat()
        return data


@dataclass
class ManifestItem:
    """One row of a lot manifest CSV (only the fields we care about)."""

    lpn: Optional[str] = None
    asin: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    department: Optional[str] = None
    description: Optional[str] = None
    condition: Optional[str] = None
    qty: int = 1
    unit_retail: float = 0.0
    weight_kg: Optional[float] = None
    pallet_id: Optional[str] = None   # physical pallet ("Pallet ID")
    box_id: Optional[str] = None      # physical box/package ("PkgID")

    @property
    def line_retail(self) -> float:
        return self.unit_retail * self.qty


@dataclass
class ManifestStats:
    """Aggregated analysis of a manifest."""

    total_items: int
    total_units: int
    total_retail: float
    avg_unit_retail: float
    categories: Dict[str, float]  # category -> retail value
    conditions: Dict[str, int]    # condition -> item count
    top_items: List[Dict] = field(default_factory=list)
