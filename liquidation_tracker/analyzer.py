"""Manifest analysis.

Parses a B-Stock manifest CSV and produces aggregate statistics: total retail,
breakdown by category and condition, average unit value and the highest-value
items. These feed both the alert rules and the human-readable reports.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from typing import Dict, List

from .models import ManifestItem, ManifestStats

# Manifest CSV headers vary slightly between lots, so we match case-insensitively
# and accept a few aliases per logical field.
_FIELD_ALIASES = {
    "lpn": ["lpn"],
    "asin": ["asin"],
    "category": ["category"],
    "subcategory": ["subcategory"],
    "description": ["item desc", "item_desc", "description"],
    "condition": ["condition"],
    "qty": ["qty", "quantity"],
    "unit_retail": ["unit retail", "unit_retail"],
    "total_retail": ["total retail", "total_retail"],
    "weight": ["itempkgweight", "item pkg weight"],
    "weight_uom": ["itempkgweightuom", "item pkg weight uom"],
}


def _build_index(fieldnames: List[str]) -> Dict[str, str]:
    """Map each logical field to the actual CSV column name present."""
    lowered = {name.lower().strip(): name for name in fieldnames}
    index: Dict[str, str] = {}
    for logical, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                index[logical] = lowered[alias]
                break
    return index


def _to_float(value: str) -> float:
    if not value:
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return 0.0


def _to_int(value: str, default: int = 1) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (ValueError, TypeError):
        return default


def parse_manifest(csv_path: str) -> List[ManifestItem]:
    """Read a manifest CSV into a list of ManifestItem."""
    items: List[ManifestItem] = []
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return items
        idx = _build_index(reader.fieldnames)

        for row in reader:
            qty = _to_int(row.get(idx.get("qty", ""), ""), default=1)
            unit_retail = _to_float(row.get(idx.get("unit_retail", ""), ""))
            if not unit_retail:
                # Fall back to total_retail / qty when unit_retail is missing.
                total = _to_float(row.get(idx.get("total_retail", ""), ""))
                unit_retail = total / qty if qty else total

            weight = _to_float(row.get(idx.get("weight", ""), ""))
            uom = (row.get(idx.get("weight_uom", ""), "") or "").lower()
            weight_kg = weight / 1000 if uom in ("gr", "g", "gram", "grams") else weight

            items.append(
                ManifestItem(
                    lpn=row.get(idx.get("lpn", "")) or None,
                    asin=row.get(idx.get("asin", "")) or None,
                    category=row.get(idx.get("category", "")) or None,
                    subcategory=row.get(idx.get("subcategory", "")) or None,
                    description=row.get(idx.get("description", "")) or None,
                    condition=row.get(idx.get("condition", "")) or None,
                    qty=qty,
                    unit_retail=unit_retail,
                    weight_kg=weight_kg or None,
                )
            )
    return items


def analyze(items: List[ManifestItem], top_n: int = 10) -> ManifestStats:
    """Aggregate a list of manifest items into ManifestStats."""
    total_units = sum(i.qty for i in items)
    total_retail = sum(i.unit_retail * i.qty for i in items)

    categories: Dict[str, float] = defaultdict(float)
    conditions: Dict[str, int] = defaultdict(int)
    for item in items:
        categories[item.category or "Unknown"] += item.unit_retail * item.qty
        conditions[item.condition or "Unknown"] += item.qty

    top_items = sorted(
        items, key=lambda i: i.unit_retail * i.qty, reverse=True
    )[:top_n]
    top_payload = [
        {
            "description": (i.description or "")[:80],
            "category": i.category,
            "condition": i.condition,
            "qty": i.qty,
            "unit_retail": round(i.unit_retail, 2),
            "line_retail": round(i.unit_retail * i.qty, 2),
        }
        for i in top_items
    ]

    return ManifestStats(
        total_items=len(items),
        total_units=total_units,
        total_retail=round(total_retail, 2),
        avg_unit_retail=round(total_retail / total_units, 2) if total_units else 0.0,
        categories={
            k: round(v, 2)
            for k, v in sorted(categories.items(), key=lambda kv: kv[1], reverse=True)
        },
        conditions=dict(sorted(conditions.items(), key=lambda kv: kv[1], reverse=True)),
        top_items=top_payload,
    )
