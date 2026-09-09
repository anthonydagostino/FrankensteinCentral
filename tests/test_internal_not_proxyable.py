"""No service can expose a container-only route through the gateway.

The exploit and its fix are in gateway/tests/test_proxy.py, which proves the
refusal itself: `/api/gmail/internal/token` used to hand a live Google OAuth
credential to any unauthenticated caller on the network, and the gateway now
404s any path with an `internal` segment (SCRUM-114).

This file guards the OTHER half, the one a behavioural test cannot reach: the
hole was never a bug in a line of code. It was a convention — "routes under
/internal/ are for other containers" — that lived in a docstring and that no
code enforced. A convention re-enforced in exactly one file drifts again the
next time somebody adds a route, and the failure is silent, because a newly
exposed route looks like every other working route.

So this scans the services for routes that read as container-only and fails
when the gateway would proxy one. It is the difference between fixing an
incident and closing the class of it.
"""
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conftest import load_service_module  # noqa: E402

gw = load_service_module("gateway_main", "gateway/app/main.py")

# Segment names that mean "other containers only". `internal` is the one in
# use; the others are here so that reaching for an obvious synonym is caught
# rather than quietly permitted.
PRIVATE_SEGMENTS = {"internal", "_internal", "private"}

ROUTE = re.compile(
    r"""@(?:app|router)\.(?:get|post|put|patch|delete|api_route)\(\s*["'](/[^"']*)["']""")


def _declared_routes() -> list[tuple[str, str]]:
    """Every route the services declare, as (file, path)."""
    found = []
    for source in sorted((ROOT / "services").rglob("*.py")):
        if "tests" in source.parts:
            continue
        rel = str(source.relative_to(ROOT))
        found += [(rel, path) for path in ROUTE.findall(source.read_text())]
    return found


def _reads_as_private(path: str) -> bool:
    return any(seg.casefold() in PRIVATE_SEGMENTS
               for seg in path.split("/") if seg)


def _client() -> TestClient:
    """A client the Host allowlist accepts (SCRUM-115), so what is asserted
    below is the internal-route refusal and not a 400 standing in front of it.
    TestClient's default `Host: testserver` is refused before the proxy runs,
    which would make these pass for the wrong reason."""
    return TestClient(gw.app, base_url="http://localhost")


def test_the_scan_actually_finds_routes():
    """A scan that matches nothing passes vacuously and reads like coverage.
    Anchor it on the route this whole ticket was about."""
    routes = _declared_routes()
    assert len(routes) > 50, "the route regex stopped matching — fix it"
    assert ("services/gmail/app/main.py", "/internal/token") in routes
    # And the client really does get past the Host check, or every assertion
    # below is a 400 wearing a refusal's clothes.
    assert _client().get("/api/gmail/health").status_code != 400


def test_the_gateway_refuses_every_container_only_route_in_the_repo():
    """Add `/private/whatever` to a service tomorrow and this goes red until
    the gateway knows to refuse it."""
    client = _client()
    exposed = []
    for source, path in _declared_routes():
        if not _reads_as_private(path):
            continue
        # Through the gateway, exactly as a browser would reach it. A refusal
        # needs no upstream, so nothing is monkeypatched and nothing is called.
        if client.get(f"/api/gmail{path}").status_code != 404:
            exposed.append(f"{source}: {path}")
    assert not exposed, (
        "these routes read as container-only but the gateway would proxy "
        "them:\n  " + "\n  ".join(exposed))


def test_the_refusal_is_not_gmail_specific():
    """The guard belongs to the proxy, not to one service. Whichever sub-app
    adds the next container-only route is covered without anyone remembering
    this ticket existed."""
    client = _client()
    for app_key in gw.REGISTRY:
        r = client.get(f"/api/{app_key}/internal/token")
        assert r.status_code == 404, app_key
        assert "access_token" not in r.text, app_key
