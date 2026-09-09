"""Container-only routes are not reachable through the gateway.

WHY THIS FILE EXISTS: `/internal/...` was a naming convention with nothing
behind it. gmail exposes

    GET /internal/token  ->  {"access_token": ..., "scopes": [...]}

a live Google credential carrying gmail.modify AND calendar.events, and its
docstring said "not meaningful to call from outside the compose network". The
gateway had never heard of that convention: it proxies any path to any
registered service and it has no authentication, so

    GET http://<box>:8080/api/gmail/internal/token

handed that credential to any unauthenticated caller on the network, over the
same origin the dashboard itself is served on (SCRUM-114).

Two tests here, doing two different jobs:

  * the behavioural ones prove the refusal is real, including the bypasses
    worth trying (casing, dot segments, non-GET methods), and that nothing
    reaches the sub-app;
  * `test_every_internal_route_in_the_repo_is_covered` scans the services for
    container-only routes and fails when one appears that the gateway's list
    does not cover. That is the half that keeps the fix true — the hole was
    not a bug in a line of code, it was a convention no code enforced, and a
    convention re-enforced only in one file drifts again the next time
    somebody adds a route.

NOT covered by this fix: gmail also publishes 8083:8000, so
http://<box>:8083/internal/token still reaches it without the gateway at all.
That is a port-publication problem, not a proxy problem — SCRUM-116.
"""
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conftest import load_service_module  # noqa: E402

gw = load_service_module("gateway_main", "gateway/app/main.py")


class ExplodingClient:
    """An upstream call is itself the failure — this never gets to make one."""

    called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, *args, **kwargs):
        ExplodingClient.called = True
        raise AssertionError("the proxy called upstream for a private route")


@pytest.fixture
def client(monkeypatch):
    ExplodingClient.called = False
    monkeypatch.setattr(gw.httpx, "AsyncClient", ExplodingClient)
    return TestClient(gw.app)


def test_the_google_credential_is_not_served_over_the_gateway(client):
    r = client.get("/api/gmail/internal/token")
    assert r.status_code == 404
    assert "access_token" not in r.text
    assert not ExplodingClient.called


def test_the_refusal_looks_like_an_absent_route_not_a_guarded_one(client):
    """403 would confirm the route exists. A scanner learns nothing from 404."""
    r = client.get("/api/gmail/internal/token")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}
    assert "internal" not in r.text.lower()


@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
def test_no_method_gets_through(client, method):
    r = getattr(client, method)("/api/gmail/internal/token")
    assert r.status_code == 404, method
    assert not ExplodingClient.called


@pytest.mark.parametrize("path", [
    "internal/token",
    "INTERNAL/token",
    "Internal/Token",
    "internal",
    "internal/",
    "deeper/internal/token",
    "internal/nested/deeper",
    "./internal/token",
    "x/../internal/token",          # httpx resolves this upstream — so must we
    "a/b/../../internal/token",
    "//internal//token",
])
def test_the_ways_around_it_are_closed(client, path):
    assert client.get(f"/api/gmail/{path}").status_code == 404, path
    assert not ExplodingClient.called, path


@pytest.mark.parametrize("app_key", ["gmail", "schedule", "core", "assistant"])
def test_the_rule_is_not_gmail_specific(client, app_key):
    """Whichever service adds the next container-only route, it is covered."""
    assert client.get(f"/api/{app_key}/internal/token").status_code == 404


def test_ordinary_paths_are_untouched(monkeypatch):
    """The guard must not cost the dashboard a single working call, including
    paths that merely CONTAIN the word — /health, /internal-notes, /internals."""

    class Ok:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b'{"ok": true}'

    class FakeClient:
        seen = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, **kwargs):
            FakeClient.seen.append(url)
            return Ok()

    monkeypatch.setattr(gw.httpx, "AsyncClient", FakeClient)
    client = TestClient(gw.app)
    for path in ("health", "internals", "internal-notes", "mail/internally",
                 "summary", "auth/login"):
        assert client.get(f"/api/gmail/{path}").status_code == 200, path
    assert len(FakeClient.seen) == 6


# --- the half that keeps it true --------------------------------------------

ROUTE = re.compile(
    r"""@(?:app|router)\.(?:get|post|put|patch|delete|api_route)\(\s*["'](/[^"']*)["']""")


def _declared_routes() -> list[tuple[Path, str]]:
    found = []
    for source in sorted((ROOT / "services").rglob("*.py")):
        if "tests" in source.parts:
            continue
        for path in ROUTE.findall(source.read_text()):
            found.append((source.relative_to(ROOT), path))
    return found


def test_the_scan_actually_finds_routes():
    """A scan that matches nothing passes vacuously. Anchor it on the route
    this whole file exists because of."""
    routes = _declared_routes()
    assert len(routes) > 50, "the route regex stopped matching — fix it"
    assert (Path("services/gmail/app/main.py"), "/internal/token") in routes


def test_every_internal_route_in_the_repo_is_covered():
    """Any route a service marks as container-only must be one the gateway
    refuses. Add `/private/whatever` tomorrow and this fails until
    PRIVATE_SEGMENTS knows about it."""
    exposed = [
        f"{source}: {path}"
        for source, path in _declared_routes()
        if not gw._reaches_private_route(path)
        and any(seg.casefold() in {"internal", "private", "_internal"}
                for seg in path.split("/") if seg)
    ]
    assert not exposed, (
        "these routes read as container-only but the gateway would proxy them; "
        "add the segment to gateway/app/main.py PRIVATE_SEGMENTS:\n  "
        + "\n  ".join(exposed))
