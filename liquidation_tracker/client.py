"""Network layer for B-Stock.

A thin ``requests.Session`` wrapper with a browser-like User-Agent. The site
sits behind Cloudflare; a plain session works from most residential IPs, but if
you hit a challenge page you can swap this class for a Playwright-backed one
without touching the rest of the pipeline (same three public methods).
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

import requests

from . import parser
from .models import Auction

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MANIFEST_URL = "https://manifest-prod.bstock.com/downloads/get"


class CloudflareChallenge(RuntimeError):
    """Raised when B-Stock returns a Cloudflare interstitial instead of content."""


class BStockClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 20,
        request_delay: float = 1.0,
        cookie: Optional[str] = None,
    ) -> None:
        self.timeout = timeout
        self.request_delay = request_delay
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
            }
        )
        # Optional logged-in session for MIXED_* manifests that require auth.
        # ``cookie`` is the raw Cookie header captured from a logged-in browser
        # (see config.BStockAuth / BSTOCK_COOKIE).
        if cookie:
            self.session.headers["Cookie"] = cookie

    def _get(self, url: str, **kwargs) -> requests.Response:
        response = self.session.get(url, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        if "Just a moment" in response.text[:2000]:
            raise CloudflareChallenge(
                f"Cloudflare challenge served for {url}. Retry later or use the "
                "Playwright client."
            )
        time.sleep(self.request_delay)
        return response

    def list_auctions(self, country: str = "ES", limit: int = 48) -> List[Auction]:
        """List active auctions for a country (ES, IT, DE, FR, ...)."""
        url = f"{parser.BASE_URL}/{parser.SITE}/?country={country}&limit={limit}"
        logger.info("Fetching auction list: %s", url)
        response = self._get(url)
        auctions = parser.parse_auction_list(response.text)
        for auction in auctions:
            if not auction.country:
                auction.country = country
        logger.info("Found %d auctions for %s", len(auctions), country)
        return auctions

    def fetch_lot_id(self, auction: Auction) -> Optional[str]:
        """Open an auction detail page and extract its manifest SKU."""
        logger.info("Fetching detail page for auction %s", auction.auction_id)
        response = self._get(auction.url)
        lot_id = parser.parse_lot_id(response.text)
        auction.lot_id = lot_id
        return lot_id

    def download_manifest(self, lot_id: str, dest_path: str) -> str:
        """Download the manifest CSV for a lot_id to ``dest_path``."""
        params = {"site": "a2z", "sku": lot_id, "file_type": "csv"}
        logger.info("Downloading manifest for %s", lot_id)
        response = self.session.get(
            MANIFEST_URL, params=params, timeout=self.timeout, stream=True
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "csv" not in content_type:
            raise RuntimeError(
                f"Manifest endpoint did not return CSV for {lot_id} "
                f"(content-type: {content_type}). The lot likely requires a "
                "logged-in session — set BSTOCK_COOKIE (see config.BStockAuth)."
            )
        with open(dest_path, "wb") as fh:
            for chunk in response.iter_content(chunk_size=8192):
                fh.write(chunk)
        logger.info("Saved manifest to %s", dest_path)
        time.sleep(self.request_delay)
        return dest_path
