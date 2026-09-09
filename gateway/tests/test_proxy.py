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
    # The gateway refuses unknown Host headers (SCRUM-115) and TestClient
    # defaults to Host: testserver. Point at localhost — a host the box
    # genuinely answers to in production — rather than allowlisting a
    # test-only name, which would be a hole that only tests can see.
    return TestClient(gw.app, base_url="http://localhost")


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


# --- SCRUM-114: the proxy is not a hole into "internal" routes ----------------
#
# The proxy forwarded ANY path to ANY registered service with no authentication
# anywhere in the chain, and gmail exposes /internal/token, which returns a live
# OAuth access token carrying gmail.modify AND calendar.events. So a single
# unauthenticated GET to the dashboard's own front door handed over read/write
# access to the whole inbox and the calendar.
#
# What "guarded" it was a docstring on the gmail route saying "internal docker
# network only". `/internal/` was a naming convention; the proxy had never
# heard of it.

def test_the_reported_exploit_is_closed(client):
    """The exact request from the ticket:
        GET http://<box>:8080/api/gmail/internal/token
    """
    FakeClient.response = FakeUpstream(
        200, {"content-type": "application/json"},
        b'{"access_token":"ya29.SECRET","scopes":"gmail.modify calendar.events"}')
    r = client.get("/api/gmail/internal/token")
    assert r.status_code == 404
    assert b"ya29.SECRET" not in r.content
    assert "access_token" not in r.text


def test_it_is_404_and_not_403(client):
    """403 confirms the route exists and is worth attacking. From outside, an
    internal route should be indistinguishable from one never written — the
    same answer an unknown app already gets."""
    FakeClient.response = FakeUpstream(200, {}, b"{}")
    internal = client.get("/api/gmail/internal/token")
    unknown = client.get("/api/nosuchapp/whatever")
    assert internal.status_code == unknown.status_code == 404


def test_the_request_is_never_forwarded_at_all(client):
    """Refused before the upstream call, not after. A gateway that asks gmail
    for the token and then declines to pass it on has still caused the token
    to be minted and put on the wire."""
    FakeClient.response = FakeUpstream(200, {}, b'{"access_token":"ya29.X"}')
    FakeClient.seen = None
    client.get("/api/gmail/internal/token")
    assert FakeClient.seen is None, "the proxy still called upstream"


@pytest.mark.parametrize("path", [
    "internal/token",
    "internal",
    "a/internal/b",
    "deep/nested/internal/token",
    "INTERNAL/token",          # case is the caller's to choose
    "Internal/Token",
    "internal/",
])
def test_every_shape_of_internal_segment_is_refused(client, path):
    FakeClient.response = FakeUpstream(200, {}, b'{"access_token":"ya29.X"}')
    assert client.get(f"/api/gmail/{path}").status_code == 404


@pytest.mark.parametrize("path", [
    "internal-ish/token",      # not the segment `internal`
    "x-internal/token",
    "internally/token",
    "needs-reply",
    "auth/login",
])
def test_ordinary_routes_are_untouched(client, path):
    """The guard matches whole segments. Blocking anything merely containing
    the substring would break real routes and teach people to work around it."""
    FakeClient.response = FakeUpstream(200, {"content-type": "application/json"}, b"{}")
    assert client.get(f"/api/gmail/{path}").status_code == 200


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_no_verb_gets_through(client, method):
    """The route is registered for five methods; a guard on GET alone would
    leave four doors open."""
    FakeClient.response = FakeUpstream(200, {}, b'{"access_token":"ya29.X"}')
    assert client.request(method, "/api/gmail/internal/token").status_code == 404


def test_no_registered_app_can_expose_an_internal_route_through_the_proxy(client):
    """Not a gmail-specific patch. Any service that adds an /internal/ route
    later is covered without anyone remembering this ticket."""
    FakeClient.response = FakeUpstream(200, {}, b"{}")
    for app_key in list(gw.REGISTRY)[:6]:
        assert client.get(f"/api/{app_key}/internal/anything").status_code == 404
