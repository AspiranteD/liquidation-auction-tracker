"""HTML parsing helpers for B-Stock auction pages.

Kept separate from the network layer so the parsing logic can be unit-tested
against saved HTML fixtures without hitting the live site.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from .models import Auction

BASE_URL = "https://bstock.com"
SITE = "amazoneu"

_RETAIL_RE = re.compile(r"Total Retail\s*€([\d.,]+)")
_PIECES_RE = re.compile(r"([\d.,]+)\s+Pieces", re.IGNORECASE)
_LOT_TYPE_RE = re.compile(
    r"(Small Truckload|Truckload|4 Pallets(?:\s+[A-Z]{2})?)", re.IGNORECASE
)
_COUNTRY_RE = re.compile(r"\b([A-Z]{2})\s+Stock\b")
_ID_RE = re.compile(r"/id/(\d+)")
_LOT_ID_RE = re.compile(r"lot_ids\s*=\s*\[\[\s*'([^']+)'")


def _to_float(raw: str) -> Optional[float]:
    if not raw:
        return None
    cleaned = raw.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_retail_value(title: str) -> Optional[float]:
    match = _RETAIL_RE.search(title or "")
    return _to_float(match.group(1)) if match else None


def parse_pieces(title: str) -> Optional[int]:
    match = _PIECES_RE.search(title or "")
    if not match:
        return None
    value = _to_float(match.group(1))
    return int(value) if value is not None else None


def parse_lot_type(title: str) -> Optional[str]:
    match = _LOT_TYPE_RE.search(title or "")
    return match.group(1).title() if match else None


def parse_country(title: str) -> Optional[str]:
    match = _COUNTRY_RE.search(title or "")
    return match.group(1) if match else None


def parse_end_time(raw: Optional[str]) -> Optional[datetime]:
    """Parse B-Stock's countdown timestamp, e.g. 'Mon, 23 Dec 2024 12:25:00 +0000'."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%a, %d %b %Y %H:%M:%S %z")
    except ValueError:
        return None


def parse_auction_list(html: str) -> List[Auction]:
    """Extract all auctions from a B-Stock listing page."""
    soup = BeautifulSoup(html, "html.parser")
    auctions: List[Auction] = []

    for item in soup.select('li[id^="auction-"]'):
        link_tag = item.select_one("a.product-image")
        href = link_tag.get("href") if link_tag else None
        if not href:
            continue
        url = href if href.startswith("http") else f"{BASE_URL}{href}"

        id_match = _ID_RE.search(href)
        if not id_match:
            continue
        auction_id = int(id_match.group(1))

        name_tag = item.select_one("div.product-name a") or item.select_one(
            "div.product-name"
        )
        title = name_tag.get_text(strip=True) if name_tag else ""

        countdown = item.select_one("div.time_remaining .countdown")
        end_time = parse_end_time(countdown.get("data-end-time") if countdown else None)

        bid_tag = item.select_one("div.current_bid span.price")
        current_bid = _to_float(
            bid_tag.get_text(strip=True).replace("€", "")
        ) if bid_tag else None

        auctions.append(
            Auction(
                auction_id=auction_id,
                title=title,
                url=url,
                country=parse_country(title),
                lot_type=parse_lot_type(title),
                retail_value=parse_retail_value(title),
                pieces=parse_pieces(title),
                current_bid=current_bid,
                end_time=end_time,
            )
        )

    return auctions


def parse_lot_id(detail_html: str) -> Optional[str]:
    """Extract the manifest SKU (lot_id) from an auction detail page."""
    soup = BeautifulSoup(detail_html, "html.parser")
    for script in soup.find_all("script"):
        if script.string and "lot_ids" in script.string:
            match = _LOT_ID_RE.search(script.string)
            if match:
                return match.group(1).upper()
    return None
