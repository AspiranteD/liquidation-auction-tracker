"""Automatic B-Stock login (FusionAuth SSO) via Playwright.

Unlocks the ``MIXED_*`` manifests that require a logged-in session, without a
hand-pasted cookie. The rest of the pipeline stays on ``requests``: we log in
once with Playwright, capture the ``.bstock.com`` session cookies and hand them
to :class:`~liquidation_tracker.client.BStockClient` as a ``Cookie`` header.

Login flow discovered 2026-07-03
--------------------------------
``https://bstock.com/amazoneu/customer/account/login/``
  → 302 → ``https://auth.bstock.com/oauth2/authorize?...`` (FusionAuth form)
     fields: ``input#loginId`` (email), ``input#password``, ``button[type=submit]`` "Login"
  → on success 302 → ``https://bstock.com/amazoneu/sso/index/login/?code=...``
     which sets the ``.bstock.com`` session cookies used by the manifest host
     (``manifest-prod.bstock.com``).

Credentials come from the environment (``BSTOCK_USER`` / ``BSTOCK_PASS``), fed
by Doppler or ``.env`` — never hard-coded. The captured cookie is cached to
``data/bstock_cookie.json`` (``data/`` is gitignored) with a TTL so we don't
drive a browser on every run.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

LOGIN_ENTRY = "https://bstock.com/amazoneu/customer/account/login/"
MARKETPLACE = "https://bstock.com/amazoneu/"
AUTH_HOST = "auth.bstock.com"          # still here after submit = NOT logged in
COOKIE_TTL_SECONDS = 6 * 3600          # re-login at most every 6 h
DEFAULT_CACHE = "data/bstock_cookie.json"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _cookies_to_header(cookies, host_suffix: str = "bstock.com") -> str:
    """Serialize the cookies that apply to ``*.bstock.com`` into a Cookie header.

    ``requests`` sends whatever we put in the static ``Cookie`` header to every
    host, so we only keep the marketplace-domain cookies (which cover the
    ``manifest-prod`` subdomain) and drop unrelated ones (OneTrust, auth-host).
    """
    parts, seen = [], set()
    # Domain cookies ('.bstock.com') first so they win over host-only dupes.
    for c in sorted(cookies, key=lambda c: 0 if c.get("domain", "").startswith(".") else 1):
        dom = c.get("domain", "").lstrip(".")
        if not (dom == host_suffix or dom.endswith("." + host_suffix)):
            continue
        name = c.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        parts.append(f"{name}={c.get('value', '')}")
    return "; ".join(parts)


def login_fetch_cookies(
    user: str,
    password: str,
    *,
    headless: bool = True,
    timeout_ms: int = 45000,
) -> str:
    """Drive a headless browser through the FusionAuth login and return the
    ``.bstock.com`` session cookies as a ``Cookie:`` header string.

    Raises ``RuntimeError`` if we don't end up authenticated (bad credentials,
    captcha or an unexpected 2FA prompt).
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=_UA, locale="es-ES")
        page = ctx.new_page()
        try:
            page.goto(LOGIN_ENTRY, wait_until="domcontentloaded", timeout=timeout_ms)
            # Dismiss the OneTrust cookie banner so it can't cover the form.
            try:
                page.click("#onetrust-accept-btn-handler", timeout=4000)
            except Exception:
                pass
            page.wait_for_selector("#loginId", timeout=timeout_ms)
            page.fill("#loginId", user)
            page.fill("#password", password)
            page.click("button[type=submit]:has-text('Login')")
            # Success = we leave the auth host and land back on the marketplace.
            try:
                page.wait_for_url(lambda url: AUTH_HOST not in url, timeout=timeout_ms)
            except Exception:
                pass
            page.wait_for_timeout(2500)
            # Touch the marketplace once so the session cookie is materialized.
            try:
                page.goto(MARKETPLACE, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(1200)
            except Exception:
                pass
            final_url = page.url
            cookies = ctx.cookies()
        finally:
            browser.close()

    if AUTH_HOST in final_url:
        raise RuntimeError(
            "Login no completado: seguimos en auth.bstock.com. Causas probables: "
            "credenciales incorrectas, captcha o verificación. Reintenta con "
            "headless=False para ver qué pide la página."
        )
    header = _cookies_to_header(cookies)
    if not header:
        raise RuntimeError(
            "Login sin cookies de sesión .bstock.com (posible bloqueo Cloudflare)."
        )
    logger.info("Login B-Stock OK (%d cookies de sesión capturadas).", header.count("=") )
    return header


def get_session_cookie(
    settings,
    *,
    force: bool = False,
    headless: bool = True,
    cache_path: Optional[str] = None,
) -> Optional[str]:
    """Resolve a logged-in Cookie header, in priority order:

    1. ``BSTOCK_COOKIE`` (manual paste) — always wins, zero browser.
    2. Cached cookie from a recent auto-login (< TTL).
    3. Fresh Playwright login with ``BSTOCK_USER`` / ``BSTOCK_PASS``.

    Returns ``None`` (never raises) when no auth is configured, so callers can
    fall back to public-only lots. A login *failure* with creds present does
    raise, so the dedicated ``login`` command surfaces the reason.
    """
    if settings.auth.cookie:
        return settings.auth.cookie

    user = os.getenv("BSTOCK_USER")
    password = os.getenv("BSTOCK_PASS")
    if not (user and password):
        return None

    cache_path = cache_path or os.getenv("BSTOCK_COOKIE_CACHE", DEFAULT_CACHE)
    if not force and cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                blob = json.load(fh)
            if blob.get("cookie") and (time.time() - blob.get("ts", 0)) < COOKIE_TTL_SECONDS:
                logger.info("Usando cookie de sesión cacheada (%s).", cache_path)
                return blob["cookie"]
        except Exception:
            pass  # corrupt/old cache → re-login

    header = login_fetch_cookies(user, password, headless=headless)
    if cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump({"ts": time.time(), "cookie": header}, fh)
        except Exception as exc:
            logger.warning("No pude cachear la cookie: %s", exc)
    return header
