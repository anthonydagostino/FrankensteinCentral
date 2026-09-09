"""The gateway must refuse a Host header it does not answer to.

WHY THIS FILE EXISTS (SCRUM-115): the gateway had no TrustedHostMiddleware, no
CORSMiddleware and no Host check anywhere, which makes an unauthenticated LAN
service the textbook target for DNS REBINDING.

The attack, because the mitigation looks arbitrary without it: a page you visit
— an ad iframe is enough — re-resolves its OWN domain to this box's private
address shortly after loading. The browser still believes it is talking to that
domain, so requests are SAME-ORIGIN, and same-origin means the script can READ
THE RESPONSE. The blast radius is not "devices on the LAN". It is any page in
any browser on any device on the LAN.

What breaks the attack is that the browser sends the ATTACKER'S DOMAIN in
`Host` (that is what it navigated to) while the packet arrives here. It cannot
forge a Host it never navigated to. So refusing unknown names refuses the
attack, while IP literals and localhost — which a browser only sends when
someone deliberately typed them — keep working.

This closes the REMOTE path. Someone already on the LAN who knows the address
is SCRUM-98 (bind to Tailscale, add auth) and is not claimed here.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from conftest import load_service_module  # noqa: E402

gw = load_service_module("gateway_main", "gateway/app/main.py")


@pytest.fixture
def client():
    return TestClient(gw.app, base_url="http://localhost")


# ── the unit, exercised directly: no server, no ambiguity ───────────────────

@pytest.mark.parametrize("host", [
    "localhost", "localhost:8080", "app.localhost",
    "127.0.0.1", "127.0.0.1:8080",
    "192.168.1.50", "192.168.1.50:8080",     # the DHCP LAN address
    "10.0.0.4:8080", "172.17.0.1",
    "[::1]", "[::1]:8080", "::1",
])
def test_addresses_the_box_answers_to_are_allowed(host):
    assert gw.host_is_allowed(host) is True, host


@pytest.mark.parametrize("host", [
    "evil.com", "evil.com:8080",              # the rebinding attacker
    "frankenstein.evil.com",
    "localhost.evil.com",                     # suffix trickery, not a subdomain of ours
    "192.168.1.50.evil.com",                  # looks like an IP, is a name
    "", "   ", ":8080",
])
def test_names_the_box_does_not_answer_to_are_refused(host):
    assert gw.host_is_allowed(host) is False, host


def test_a_duplicated_host_header_is_judged_on_the_first_value():
    """Sent comma-joined. Smuggling an allowed value behind an attacker's must
    not launder the request."""
    assert gw.host_is_allowed("evil.com, localhost") is False
    assert gw.host_is_allowed("localhost, evil.com") is True


def test_a_configured_name_is_allowed(monkeypatch):
    """A Tailscale name or hosts-file alias, via GATEWAY_ALLOWED_HOSTS."""
    monkeypatch.setattr(gw, "ALLOWED_HOSTS", ["box.tail1234.ts.net"])
    assert gw.host_is_allowed("box.tail1234.ts.net:8080") is True
    assert gw.host_is_allowed("other.ts.net") is False


# ── end to end, through the real middleware stack ──────────────────────────

@pytest.mark.parametrize("path", ["/api/apps", "/api/gmail/health", "/"])
def test_a_rebinding_request_is_refused_whatever_it_asks_for(client, path):
    r = client.get(path, headers={"Host": "evil.com"})
    assert r.status_code == 400
    assert "invalid host header" in r.text


def test_a_refused_request_is_never_forwarded_upstream(client, monkeypatch):
    """Refusing the response is not enough. A request we are about to reject
    must not first be proxied — fetching a secret and then declining to hand it
    over is still a fetch, and on a slow upstream it is still a timing signal.

    (This holds by construction: http middleware runs ahead of every route
    handler, and the proxy is a route handler. Pinned anyway, because the day
    someone moves this check into the proxy itself is the day it stops being
    true and nothing else would notice.)"""
    reached = []

    class Upstream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, *args, **kwargs):
            reached.append(args)
            raise RuntimeError("should never be called")

    monkeypatch.setattr(gw.httpx, "AsyncClient", Upstream)
    r = client.get("/api/gmail/health", headers={"Host": "evil.com"})
    assert r.status_code == 400
    assert reached == [], "the proxy ran before the host was checked"


def test_ordinary_traffic_is_untouched(client):
    """The check must not become a general outage."""
    r = client.get("/api/apps")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
