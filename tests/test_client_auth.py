"""BSTOCK_COOKIE auth hook: config parsing + client header wiring."""
from liquidation_tracker.client import BStockClient, sku_candidates
from liquidation_tracker.config import BStockAuth, Settings


def test_sku_candidates_esbx_is_uppercase():
    # ESBX (and most types) use the all-uppercase sku — the first candidate hits.
    assert sku_candidates("a2z_cr_es_20260629_esbx1_012")[0] == "A2Z_CR_ES_20260629_ESBX1_012"


def test_sku_candidates_mixed_falls_back_to_titlecase():
    # MIXED lots are stored title-cased ("Mixed"); uppercasing 302s to /oops, so
    # the title-cased lot-type token must be offered as a fallback candidate.
    cands = sku_candidates("a2z_cr_it_20260629_mixed_005")
    assert cands == ["A2Z_CR_IT_20260629_MIXED_005", "A2Z_CR_IT_20260629_Mixed_005"]


def test_bstock_auth_reads_cookie_from_env(monkeypatch):
    monkeypatch.setenv("BSTOCK_COOKIE", "  session=abc; csrf=xyz  ")
    auth = BStockAuth.from_env()
    assert auth.cookie == "session=abc; csrf=xyz"
    assert auth.configured is True


def test_bstock_auth_absent_is_unconfigured(monkeypatch):
    monkeypatch.delenv("BSTOCK_COOKIE", raising=False)
    auth = BStockAuth.from_env()
    assert auth.cookie is None
    assert auth.configured is False


def test_settings_expose_auth(monkeypatch):
    monkeypatch.setenv("BSTOCK_COOKIE", "session=abc")
    assert Settings.from_env().auth.cookie == "session=abc"


def test_client_sets_cookie_header():
    client = BStockClient(cookie="session=abc; csrf=xyz")
    assert client.session.headers.get("Cookie") == "session=abc; csrf=xyz"


def test_client_without_cookie_has_no_cookie_header():
    client = BStockClient()
    assert "Cookie" not in client.session.headers
