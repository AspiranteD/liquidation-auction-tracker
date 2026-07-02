"""BSTOCK_COOKIE auth hook: config parsing + client header wiring."""
from liquidation_tracker.client import BStockClient
from liquidation_tracker.config import BStockAuth, Settings


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
