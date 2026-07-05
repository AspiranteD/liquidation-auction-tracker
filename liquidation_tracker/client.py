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


def sku_candidates(lot_id: str) -> List[str]:
    """Case variants of a lot's manifest sku, in the order to try.

    The manifest endpoint (``manifest-prod.bstock.com``) is CASE-SENSITIVE and
    each lot-type has its own canonical casing — there is no universal rule:

    * ``ESBX`` and most types are ALL-UPPERCASE  → ``A2Z_CR_ES_..._ESBX1_012``
    * ``MIXED`` lots are TITLE-CASED             → ``A2Z_CR_IT_..._Mixed_005``

    The page only builds the real sku in JavaScript, so instead of rendering we
    try uppercase first (covers ESBX & friends in one request) and fall back to
    title-casing the lot-type token (covers "Mixed"). Getting this wrong is what
    made MIXED manifests 302 to ``/oops`` and look like an auth wall — they are
    actually public.
    """
    upper = lot_id.upper()
    variants = [upper]
    parts = upper.split("_")
    if len(parts) >= 2:
        parts[-2] = parts[-2].capitalize()  # MIXED -> Mixed (ESBX1 -> Esbx1, unused)
        variants.append("_".join(parts))
    out, seen = [], set()
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


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
        """Download the manifest CSV for a lot to ``dest_path``.

        The manifest is PUBLIC (no login needed). The only catch is that the
        endpoint is case-sensitive and each lot-type has its own canonical
        casing, so we try the variants from :func:`sku_candidates`.
        """
        last_ct = ""
        for sku in sku_candidates(lot_id):
            logger.info("Downloading manifest for %s", sku)
            response = self.session.get(
                MANIFEST_URL,
                params={"site": "a2z", "sku": sku, "file_type": "csv"},
                timeout=self.timeout,
                stream=True,
            )
            response.raise_for_status()
            last_ct = response.headers.get("content-type", "")
            if "csv" in last_ct:
                with open(dest_path, "wb") as fh:
                    for chunk in response.iter_content(chunk_size=8192):
                        fh.write(chunk)
                logger.info("Saved manifest to %s", dest_path)
                time.sleep(self.request_delay)
                return dest_path
            response.close()  # wrong-case sku → /oops HTML; try the next variant
            time.sleep(self.request_delay)
        raise RuntimeError(
            f"Manifest endpoint no devolvió CSV para {lot_id} "
            f"(probé {sku_candidates(lot_id)}; último content-type: {last_ct}). "
            "El lote puede haber cerrado o cambiado de formato."
        )
