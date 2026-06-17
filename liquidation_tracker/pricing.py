"""Real-price resolution for suspected misclassified items ("regalados").

A premium product declared at an absurd price (an iPhone at 14 €) is only a
*suspect*. Instead of dumping "dudosos" on the user, we resolve the real market
price ourselves, in cost order:

    1. local cache (data/price_cache.json)  — instant, free
    2. Reusalia DB (physical_item.scraped_price / sale.final_price) — free, ~27k ASINs
    3. live Amazon scrape (amazon.es)        — best-effort, Amazon soft-blocks often

Everything degrades gracefully: no DB driver, no network, or an antibot block
never raises — the resolver just returns a lower-confidence answer or ``None``.

The AmazonScraper is lifted from the Reusalia backend
(scripts/services/amazon_scraper.py) so it runs standalone, with no backend or
DB required.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

DEFAULT_ENV = r"C:\Users\guill\CursorProjects\_ARCHIVADO_reusalia-backend_usar_carpeta_Claude\.env"
CACHE_PATH = "data/price_cache.json"
AMAZON_URL = "https://www.amazon.es/dp/{asin}"

# Lifted defaults from the backend's UserAgentManager fallback list.
DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]

# Amazon block/captcha markers (absent from a real 404 or a valid product page).
_BLOCK_MARKERS = (
    "captcha", "robot check", "automated access", "enter the characters you see",
    "validatecaptcha", "sorry, we just need to make sure", "discuss automated access",
)
# A real product page is >1MB; a captcha/soft-block is ~5KB.
_SMALL_BODY = 200_000


@dataclass
class ResolvedPrice:
    asin: str
    price: Optional[float]
    source: str        # cache | db_scraped | db_sale | amazon | none | blocked
    confidence: str    # alta | media | baja

    @property
    def found(self) -> bool:
        return self.price is not None


# ---------------------------------------------------------------------------
# Amazon scraper (standalone, lifted from the backend)
# ---------------------------------------------------------------------------

class AmazonScraper:
    """Extracts the listing price of an ASIN from amazon.es. Returns a dict with
    ``_status`` in {200, 404, "blocked", "error"} so callers can distinguish a
    genuinely missing ASIN from an antibot block."""

    def __init__(self, user_agents: Optional[List[str]] = None) -> None:
        import requests
        self._user_agents = user_agents or DEFAULT_USER_AGENTS
        self._session = requests.Session()
        self._warmed = False

    def _headers(self) -> dict:
        return {
            "User-Agent": random.choice(self._user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
            "Referer": "https://www.amazon.es/",
        }

    def _warm(self) -> None:
        if self._warmed:
            return
        self._warmed = True
        try:
            self._session.get("https://www.amazon.es/", headers=self._headers(), timeout=12)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Warm-up falló (no crítico): %s", exc)

    def scrape_price(self, asin: str, timeout: int = 15) -> dict:
        from bs4 import BeautifulSoup
        self._warm()
        url = AMAZON_URL.format(asin=asin)
        try:
            resp = self._session.get(url, headers=self._headers(), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - network failure, retry later
            logger.debug("Scrape ASIN %s falló: %s", asin, exc)
            return {"_status": "error"}

        body = resp.text
        if any(m in body.lower() for m in _BLOCK_MARKERS):
            return {"_status": "blocked"}
        if resp.status_code == 404:
            return {"_status": 404}

        soup = BeautifulSoup(resp.content, "html.parser")
        title = soup.find("span", id="productTitle")
        if not title and len(body) < _SMALL_BODY:
            return {"_status": "blocked"}  # 200 but empty+small = soft-block

        return {"_status": 200, "price": self._price(soup),
                "title": title.get_text(strip=True) if title else ""}

    @staticmethod
    def _price(soup) -> Optional[float]:
        for cid in ("corePriceDisplay_desktop_feature_div", "corePrice_feature_div"):
            container = soup.find(id=cid)
            if not container:
                continue
            whole = container.find("span", class_="a-price-whole")
            frac = container.find("span", class_="a-price-fraction")
            if whole:
                try:
                    euros = int(whole.text.strip().replace(".", "").replace(",", "").replace("€", ""))
                    cents = int(frac.text.strip()) if frac and frac.text.strip().isdigit() else 0
                    return euros + cents / 100.0
                except ValueError:
                    continue
        return None


# ---------------------------------------------------------------------------
# Reusalia DB price lookup (optional)
# ---------------------------------------------------------------------------

class ReusaliaDB:
    """Read-only median Amazon price (and real sale price) per ASIN from the
    Reusalia DB. Silently inert if psycopg2 or the DB is unavailable."""

    def __init__(self, env_path: str = DEFAULT_ENV) -> None:
        self._url = self._read_url(env_path)
        self._conn = None
        self._failed = self._url is None

    @staticmethod
    def _read_url(env_path: str) -> Optional[str]:
        try:
            with open(env_path, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("DATABASE_URL="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            return None
        return None

    def _cursor(self):
        if self._failed:
            return None
        if self._conn is None:
            try:
                import psycopg2
                self._conn = psycopg2.connect(self._url)
                self._conn.set_session(readonly=True, autocommit=True)
            except Exception as exc:  # noqa: BLE001 - DB optional
                logger.info("BD Reusalia no disponible (%s); sigo sin ella.", exc)
                self._failed = True
                return None
        try:
            return self._conn.cursor()
        except Exception:  # noqa: BLE001
            self._failed = True
            return None

    def scraped_price(self, asin: str) -> Optional[float]:
        cur = self._cursor()
        if cur is None:
            return None
        try:
            cur.execute(
                "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY scraped_price) "
                "FROM physical_item WHERE asin=%s AND scraped_price IS NOT NULL",
                (asin,),
            )
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("Lookup scraped_price %s falló: %s", asin, exc)
            return None
        finally:
            cur.close()

    def sale_price(self, asin: str) -> Optional[float]:
        cur = self._cursor()
        if cur is None:
            return None
        try:
            cur.execute(
                "SELECT AVG(s.final_price) FROM sale s "
                "JOIN physical_item p ON p.lpn = s.lpn "
                "WHERE p.asin=%s AND s.final_price IS NOT NULL",
                (asin,),
            )
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("Lookup sale_price %s falló: %s", asin, exc)
            return None
        finally:
            cur.close()


# ---------------------------------------------------------------------------
# Resolver: cache -> DB -> scrape
# ---------------------------------------------------------------------------

class PriceResolver:
    def __init__(
        self,
        cache_path: str = CACHE_PATH,
        use_db: bool = True,
        enable_scrape: bool = True,
        env_path: str = DEFAULT_ENV,
        max_blocks: int = 6,
        scrape_delay: float = 2.0,
        scraper: Optional[AmazonScraper] = None,
        db: Optional[ReusaliaDB] = None,
    ) -> None:
        self.cache_path = cache_path
        self.enable_scrape = enable_scrape
        self.max_blocks = max_blocks
        self.scrape_delay = scrape_delay
        self._cache = self._load_cache(cache_path)
        self._cache_dirty = False
        self._consecutive_blocks = 0
        self._scraper = scraper
        self._db = db if db is not None else (ReusaliaDB(env_path) if use_db else None)

    @staticmethod
    def _load_cache(path: str) -> dict:
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def save_cache(self) -> None:
        if not self._cache_dirty:
            return
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as fh:
            json.dump(self._cache, fh, indent=1, ensure_ascii=False)
        self._cache_dirty = False

    def _remember(self, asin: str, price: Optional[float], source: str) -> None:
        if price is not None:
            self._cache[asin] = {"price": price, "source": source}
            self._cache_dirty = True

    def resolve(self, asin: Optional[str]) -> ResolvedPrice:
        if not asin:
            return ResolvedPrice("", None, "none", "baja")

        cached = self._cache.get(asin)
        if cached and cached.get("price") is not None:
            return ResolvedPrice(asin, float(cached["price"]), "cache", "alta")

        if self._db is not None:
            db_price = self._db.scraped_price(asin)
            if db_price is not None:
                self._remember(asin, db_price, "db_scraped")
                return ResolvedPrice(asin, db_price, "db_scraped", "alta")

        if self.enable_scrape and self._consecutive_blocks < self.max_blocks:
            scraped = self._scrape(asin)
            if scraped is not None:
                self._remember(asin, scraped, "amazon")
                return ResolvedPrice(asin, scraped, "amazon", "alta")

        # Last resort: our own realized sale price (a lower bound on value).
        if self._db is not None:
            sale = self._db.sale_price(asin)
            if sale is not None:
                self._remember(asin, sale, "db_sale")
                return ResolvedPrice(asin, sale, "db_sale", "media")

        return ResolvedPrice(asin, None, "none", "baja")

    def _scrape(self, asin: str) -> Optional[float]:
        if self._scraper is None:
            try:
                self._scraper = AmazonScraper()
            except Exception as exc:  # noqa: BLE001 - requests/bs4 missing
                logger.info("Scraper no disponible (%s); sigo sin scraping.", exc)
                self.enable_scrape = False
                return None
        data = self._scraper.scrape_price(asin)
        status = data.get("_status")
        if status == "blocked":
            self._consecutive_blocks += 1
            if self._consecutive_blocks >= self.max_blocks:
                logger.warning(
                    "Amazon bloquea (%d seguidos): desactivo scraping este pase.",
                    self._consecutive_blocks,
                )
            return None
        self._consecutive_blocks = 0
        if self.scrape_delay:
            time.sleep(self.scrape_delay)
        if status == 200:
            return data.get("price")
        return None
