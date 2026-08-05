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
# The country suffix ("4 Pallets DE") must stay case-sensitive: with IGNORECASE
# it would swallow the "of" in "4 Pallets of Auto Goods" and produce an unknown
# lot type whose transport cost resolves to 0.
# B-Stock also sells part loads ("2 Pallets of ...", "3 Pallets of ..."). They
# used to fall through as an unknown type, which made transport resolve to 0 and
# the max bid come out too high — they ride the same truck as a 4-pallet lot and
# cost the same.
_LOT_TYPE_RE = re.compile(
    r"(Small Truckload|Truckload|[1-4] Pallets?(?-i:\s+[A-Z]{2}\b)?)", re.IGNORECASE
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
                # The page ships lot_ids lowercase; we normalize to uppercase
                # here (stable filenames/keys). The manifest endpoint is
                # CASE-SENSITIVE per lot-type, so the actual case variants are
                # resolved at download time (see client._sku_candidates).
                return match.group(1).upper()
    return None


_FIXED_PRICE_RE = re.compile(
    r'id=["\']isFixedPrice["\'][^>]*value=["\']([^"\']*)["\']', re.I
)
_HEADLINE_PRICE_RE = re.compile(
    r'id=["\']current_bid_amount["\'][^>]*>(.*?)</span>', re.I | re.S
)
_HEADLINE_LABEL_RE = re.compile(
    r'id=["\']current_bid_label["\'][^>]*>(.*?)</div>', re.I | re.S
)


def parse_is_fixed_price(detail_html: str) -> bool:
    """True when the lot is sold at a fixed 'Buy Now' price, not auctioned.

    These lots never appear in the active auction listing, so without this the
    caller sees no bid at all and reads the lot as 'sin pujas' — the exact
    opposite of the truth, since the price is already final and take-it-or-
    leave-it."""
    match = _FIXED_PRICE_RE.search(detail_html or "")
    return bool(match) and match.group(1).strip().lower() in ("true", "1")


def parse_headline_price(detail_html: str) -> Optional[float]:
    """The big number on a lot page: the current bid, or the Buy Now price on a
    fixed-price lot. The label right above it says which (parse_is_fixed_price).
    Works logged out, which is when the listing metadata is unavailable."""
    match = _HEADLINE_PRICE_RE.search(detail_html or "")
    if not match:
        return None
    # Strip the currency symbol (its encoding varies) and any markup.
    raw = re.sub(r"<[^>]+>", "", match.group(1))
    digits = re.search(r"[\d][\d,.]*", raw)
    return _to_float(digits.group(0)) if digits else None


def parse_headline_label(detail_html: str) -> Optional[str]:
    """'Buy Now' or 'Current bid' — whatever the page prints above the price."""
    match = _HEADLINE_LABEL_RE.search(detail_html or "")
    if not match:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(1))).strip() or None
