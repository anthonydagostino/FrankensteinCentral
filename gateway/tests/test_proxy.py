"""The gateway proxy's handling of redirects.

WHY THIS FILE EXISTS: the proxy copied an upstream response's body, status and
content-type and nothing else. A redirect is nothing BUT its Location header,
so an upstream 307 reached the browser as a 307 pointing nowhere, and the
browser did nothing at all.

That is not an abstract gap. `/api/gmail/auth/login` is a redirect to Google's
consent screen, and re-consenting is the one repair for a Google Calendar
credential that was granted mail access only — so the single fix the dashboard
can offer was silently inert through the front door the dashboard is served on.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conftest import load_service_module  # noqa: E402

gw = load_service_module("gateway_main", "gateway/app/main.py")

GOOGLE = ("https://accounts.google.com/o/oauth2/v2/auth?client_id=x&"
          "scope=gmail.modify+calendar.events")


class FakeUpstream:
    """Enough of an httpx response for the proxy to copy."""

    def __init__(self, status_code, headers, content=b""):
        self.status_code = status_code
        self.headers = headers
        self.content = content


class FakeClient:
    response = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, *args, **kwargs):
        FakeClient.seen = kwargs
        return FakeClient.response


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(gw.httpx, "AsyncClient", FakeClient)
    return TestClient(gw.app)


def test_a_redirect_keeps_the_only_thing_it_carries(client):
    FakeClient.response = FakeUpstream(307, {"location": GOOGLE})
    r = client.get("/api/gmail/auth/login", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == GOOGLE


def test_every_redirect_status_is_forwarded_intact(client):
    for code in (301, 302, 303, 307, 308):
        FakeClient.response = FakeUpstream(code, {"location": GOOGLE})
        r = client.get("/api/gmail/auth/login", follow_redirects=False)
        assert r.status_code == code
        assert r.headers["location"] == GOOGLE, code


def test_an_ordinary_response_is_unchanged(client):
    """The fix must not start inventing a Location on responses without one."""
    FakeClient.response = FakeUpstream(
        200, {"content-type": "application/json"}, b'{"ok": true}')
    r = client.get("/api/gmail/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert "location" not in {k.lower() for k in r.headers}


def test_an_unknown_app_is_still_rejected_before_any_call(client):
    r = client.get("/api/nosuchapp/health")
    assert r.status_code == 404


def test_a_form_post_reaches_the_sub_app_intact(client):
    """The "Finish connecting" button on the Gmail connect page posts through
    here. If the body or its content-type were dropped, the paste would arrive
    empty and the rescue flow would fail in the one situation it exists for."""
    FakeClient.response = FakeUpstream(200, {"content-type": "text/html"}, b"<h2>ok</h2>")
    r = client.post("/api/gmail/auth/finish", data={"pasted": "4/some-code"})
    assert r.status_code == 200
    assert FakeClient.seen["content"] == b"pasted=4%2Fsome-code"
    sent = {k.lower(): v for k, v in FakeClient.seen["headers"].items()}
    assert sent["content-type"] == "application/x-www-form-urlencoded"


def test_html_from_a_sub_app_is_served_as_html(client):
    """The connect page is HTML, not JSON. A dropped content-type would render
    it as plain text — every tag visible, no form to submit."""
    FakeClient.response = FakeUpstream(
        200, {"content-type": "text/html; charset=utf-8"}, b"<h2>Connect Google</h2>")
    r = client.get("/api/gmail/auth/login")
    assert r.headers["content-type"].startswith("text/html")
