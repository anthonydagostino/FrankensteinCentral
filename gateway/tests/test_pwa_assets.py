"""The install-to-home-screen assets are actually reachable, at the right scope.

WHY THIS FILE EXISTS: PRODUCT_IDEAS #7. A manifest that 404s, or a service
worker served from a subdirectory, fails silently — the browser simply declines
to install and says nothing. The failure mode of this whole feature is
"nothing happens, quietly", so the parts that must be reachable are asserted
rather than eyeballed.

The scope rule is the one people get wrong: a worker at /static/sw.js can only
control /static/*, so it would never see a navigation to "/" and the offline
render would never fire.
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conftest import load_service_module  # noqa: E402

gw = load_service_module("gateway_main", "gateway/app/main.py")
# The gateway refuses unknown Host headers (SCRUM-115) and TestClient
# defaults to Host: testserver. Point at localhost — a host the box
# genuinely answers to in production — rather than allowlisting a
# test-only name, which would be a hole that only tests can see.
client = TestClient(gw.app, base_url="http://localhost")
STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.mark.parametrize("path", ["/manifest.webmanifest", "/sw.js", "/icon.svg",
                                  "/offline.js"])
def test_every_pwa_asset_is_served(path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} is not reachable, so install fails silently"
    assert r.content.strip(), f"{path} is empty"


def test_the_service_worker_is_at_the_ROOT_so_its_scope_covers_the_hub():
    """A worker under /static/ could not control a navigation to "/"."""
    assert (STATIC / "sw.js").exists()
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "addEventListener" in r.text


def test_the_manifest_is_valid_json_with_what_a_browser_requires():
    doc = json.loads(client.get("/manifest.webmanifest").text)
    for key in ("name", "start_url", "display", "icons"):
        assert key in doc, f"manifest has no {key}; the install prompt will not appear"
    assert doc["display"] == "standalone", "it would open in a browser tab, not as an app"
    assert doc["icons"], "an icon is required for the home screen"
    for icon in doc["icons"]:
        assert client.get(icon["src"]).status_code == 200, f"{icon['src']} 404s"


def test_the_page_links_the_manifest_and_the_icon():
    html = client.get("/").text
    assert 'rel="manifest"' in html, "nothing tells the browser it is installable"
    assert "/icon.svg" in html


def test_offline_js_loads_before_home_js():
    """home.js calls Offline.staleView on every refresh; loaded after, it is
    a ReferenceError on the first paint."""
    html = client.get("/").text
    assert html.index("/offline.js") < html.index("/home.js")


def test_static_assets_still_revalidate():
    """no_stale_static exists so a deploy is picked up. The worker caches these
    too, so if this regressed both layers would serve stale JS."""
    assert client.get("/sw.js").headers.get("Cache-Control") == "no-cache"
    assert client.get("/manifest.webmanifest").headers.get("Cache-Control") == "no-cache"


def test_the_worker_never_caches_a_mutation():
    """Asserted on the CODE: a replayed or swallowed POST is the one failure
    worse than being offline — a 'workout logged' toast for a write that never
    happened."""
    sw = (STATIC / "sw.js").read_text()
    assert "isMutation" in sw
    body = sw[sw.index('addEventListener("fetch"'):]
    guard = body.index("isMutation")
    assert guard < body.index("respondWith"), \
        "the mutation guard must come before anything is answered from cache"


def test_the_worker_only_caches_the_home_payload_among_apis():
    sw = (STATIC / "sw.js").read_text()
    assert "isCacheableApi" in sw
    assert 'indexOf("/api/") === 0' in sw, \
        "other API calls must fall through to the network, never a stale cache"


def test_the_worker_does_not_touch_third_party_requests():
    sw = (STATIC / "sw.js").read_text()
    assert "url.origin !== self.location.origin" in sw
