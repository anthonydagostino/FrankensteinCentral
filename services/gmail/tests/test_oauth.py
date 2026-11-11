"""The OAuth callback goes through the gateway.

WHY THIS FILE EXISTS: the redirect URI pointed at this service's own port,
8083, which is bound to 127.0.0.1 on the box. From any other device Google's
redirect hung until it timed out and the code was never exchanged, so Google
Calendar could not be connected from a laptop or a phone. The redirect now
follows the address the dashboard was opened at, through the gateway, and the
connect page describes that address rather than a stale localhost one.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from conftest import load_service_module  # noqa: E402

gm = load_service_module("gmail_main", "services/gmail/app/main.py")

GATEWAY = {"x-forwarded-host": "192.168.1.185.nip.io:8080", "x-forwarded-proto": "http",
           "x-forwarded-prefix": "/api/gmail"}
THROUGH_GATEWAY = "http://192.168.1.185.nip.io:8080/api/gmail/auth/callback"


@pytest.fixture(autouse=True)
def configured(tmp_path, monkeypatch):
    monkeypatch.setattr(gm, "CLIENT_ID", "id")
    monkeypatch.setattr(gm, "CLIENT_SECRET", "secret")
    monkeypatch.setattr(gm, "REDIRECT_URI", "")
    monkeypatch.setattr(gm, "TOKEN_FILE", str(tmp_path / "token.json"))
    monkeypatch.setattr(gm, "TOKENS", {})
    gm._CONSENT["redirect_uri"] = ""


@pytest.fixture
def client():
    return TestClient(gm.app)


def _consent_redirect(page: str) -> str:
    import html
    import urllib.parse
    href = page.split("class='go' href='", 1)[1].split("'", 1)[0]
    query = urllib.parse.urlparse(html.unescape(href)).query
    return urllib.parse.parse_qs(query)["redirect_uri"][0]


def test_through_the_gateway_the_redirect_is_the_dashboard_address(client):
    r = client.get("/auth/login", headers=GATEWAY)
    assert r.status_code == 200
    assert _consent_redirect(r.text) == THROUGH_GATEWAY


def test_the_page_describes_the_real_redirect_not_localhost_8083(client):
    page = client.get("/auth/login", headers=GATEWAY).text
    assert THROUGH_GATEWAY in page
    assert f'placeholder="{THROUGH_GATEWAY}?code=' in page
    assert "localhost:8083" not in page
    assert "means <em>this</em> device" not in page


def test_on_its_own_port_the_redirect_is_its_own_address(client):
    page = client.get("/auth/login", headers={"host": "localhost:8083"}).text
    assert _consent_redirect(page) == "http://localhost:8083/auth/callback"
    assert "means <em>this</em> device" in page, "only true, and only said, for a localhost redirect"


def test_an_explicit_env_value_pins_the_redirect(client, monkeypatch):
    monkeypatch.setattr(gm, "REDIRECT_URI", "https://box.tailnet.ts.net/api/gmail/auth/callback")
    page = client.get("/auth/login", headers=GATEWAY).text
    assert _consent_redirect(page) == "https://box.tailnet.ts.net/api/gmail/auth/callback"


class _Resp:
    status_code = 200

    def json(self):
        return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/gmail.modify " + gm.CALENDAR_SCOPE}


class _Client:
    posted = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None, timeout=None):
        _Client.posted = data
        return _Resp()


def test_the_exchange_repeats_the_redirect_the_consent_used(client, monkeypatch):
    """Google refuses an exchange whose redirect_uri differs from the consent
    request. The paste box may be used from another address, so the value is
    the one the consent link carried, not re-derived."""
    monkeypatch.setattr(gm.httpx, "AsyncClient", _Client)
    client.get("/auth/login", headers=GATEWAY)
    r = client.post("/auth/finish", data={"pasted": f"{THROUGH_GATEWAY}?code=4%2Fabc&scope=x"},
                    headers={"host": "192.168.1.185:8080"})
    assert r.status_code == 200 and "Google connected" in r.text
    assert _Client.posted["redirect_uri"] == THROUGH_GATEWAY
    assert _Client.posted["code"] == "4/abc"


def test_the_callback_through_the_gateway_completes_the_connection(client, monkeypatch):
    monkeypatch.setattr(gm.httpx, "AsyncClient", _Client)
    client.get("/auth/login", headers=GATEWAY)
    r = client.get("/auth/callback", params={"code": "4/xyz", "scope": "s"}, headers=GATEWAY)
    assert r.status_code == 200 and "Google connected" in r.text
    assert "Google Calendar access granted" in r.text
    assert _Client.posted["redirect_uri"] == THROUGH_GATEWAY
    assert gm.TOKENS["refresh_token"] == "rt"
