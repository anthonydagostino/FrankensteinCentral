"""Tests for the post-deploy readiness check (scripts/readiness.sh).

Started containers are not a working application. This check answers "is the
core dashboard actually serving?" and it must do so:

  * READ-ONLY -- GET only, and never a sync/email/calendar/financial endpoint
  * BOUNDED   -- one shared time budget; it can never hang a deploy
  * HONEST    -- an unconfigured third-party integration is DEGRADED and must
                 never fail a core deployment
  * SPECIFIC  -- a 200 that is not this dashboard must fail. The first version
                 accepted any page containing "<html" and "app", so
                 "<html><body>Application unavailable</body></html>" passed.
                 That false positive is the reason most of this file exists.
"""
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "scripts" / "readiness.sh"

MARKERS = ["cc-grid", "cc-money", "cc-donext", "cc-today"]

# A page that looks like the real dashboard: the markers plus its own assets.
GOOD_INDEX = (
    "<!doctype html><html><head>"
    '<link rel="stylesheet" href="/styles.css" />'
    '</head><body>'
    + "".join(f'<div id="{m}"></div>' for m in MARKERS)
    + '<script src="/home.js"></script>'
    "</body></html>"
).encode()

# Codex's reproduction: a 200 error page. It contains "<html" and the word
# "Application", which the original substring check accepted.
ERROR_PAGE = b"<html><body>Application unavailable</body></html>"

REQUIRED = ["core", "tasks"]
OPTIONAL = ["gmail", "firefly"]

GOOD_JS = b"(function(){window.hub=1;})();\n"
GOOD_CSS = b":root{--x:1}\n"


def healthy(extra=None):
    h = {k: {"key": k, "status": "up"} for k in REQUIRED + OPTIONAL}
    if extra:
        h.update(extra)
    return h


def make_server(index=None, apps=None, health=None, assets=None,
                status_for=None, health_sequence=None):
    """A stub gateway that records every request it receives."""
    seen = []
    index = GOOD_INDEX if index is None else index
    if apps is None:
        apps = [{"key": "core", "name": "Core"}, {"key": "tasks", "name": "Tasks"}]
    if health is None and health_sequence is None:
        health = healthy()
    if assets is None:
        assets = {"/home.js": (200, GOOD_JS, "application/javascript"),
                  "/styles.css": (200, GOOD_CSS, "text/css")}
    state = {"health_calls": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            seen.append(("GET", self.path))
            code = (status_for or {}).get(self.path, 200)
            if self.path == "/":
                self._send(code, index, "text/html")
            elif self.path == "/api/apps":
                self._send(code, json.dumps(apps).encode())
            elif self.path == "/api/health":
                if health_sequence is not None:
                    i = min(state["health_calls"], len(health_sequence) - 1)
                    state["health_calls"] += 1
                    payload = health_sequence[i]
                else:
                    payload = health
                self._send(code, json.dumps(payload).encode())
            elif self.path in assets:
                st, body, ctype = assets[self.path]
                self._send(st, body, ctype)
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            seen.append(("POST", self.path))
            self._send(405, b"{}")

        do_PUT = do_DELETE = do_PATCH = do_POST

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def run_check(srv, **env_over):
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "FRANKENSTEIN_READINESS_URL": f"http://127.0.0.1:{srv.server_port}",
        "FRANKENSTEIN_READINESS_BUDGET": "4",
        "FRANKENSTEIN_READINESS_DELAY": "0.2",
        "FRANKENSTEIN_READINESS_TIMEOUT": "2",
        "FRANKENSTEIN_REQUIRED_SERVICES": " ".join(REQUIRED),
        "FRANKENSTEIN_OPTIONAL_SERVICES": " ".join(OPTIONAL),
        "FRANKENSTEIN_READINESS_MARKERS": " ".join(MARKERS),
    }
    env.update(env_over)
    r = subprocess.run(["bash", str(READINESS), "--json-only"],
                       capture_output=True, text=True, timeout=120, env=env)
    return r, json.loads(r.stdout)


# == the genuine hub ====================================================

def test_a_healthy_stack_passes():
    srv, _ = make_server()
    try:
        r, doc = run_check(srv)
        assert r.returncode == 0, r.stderr
        assert doc["result"] == "pass"
        assert doc["required_failed"] == []
        assert doc["degraded"] == []
    finally:
        srv.shutdown()


# == the false positive Codex reproduced ================================

def test_a_200_error_page_containing_Application_fails():
    """THE REGRESSION. '<html' plus 'app' is not evidence of a dashboard."""
    srv, _ = make_server(index=ERROR_PAGE)
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1, "a 200 error page passed as a working hub"
        assert doc["result"] == "fail"
        assert "gateway serves the dashboard" in doc["required_failed"]
    finally:
        srv.shutdown()


@pytest.mark.parametrize("drop", MARKERS)
def test_every_required_marker_is_actually_required(drop):
    """Each marker must be load-bearing, or the check is theatre."""
    page = GOOD_INDEX.replace(f'<div id="{drop}"></div>'.encode(), b"")
    srv, _ = make_server(index=page)
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1, f"a page missing {drop} was accepted"
        assert drop in json.dumps(doc)
    finally:
        srv.shutdown()


# == the entry page's own assets ========================================

def test_a_missing_script_fails():
    """index.html served while its JavaScript 404s renders nothing."""
    srv, _ = make_server(assets={"/styles.css": (200, GOOD_CSS, "text/css")})
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1
        assert any("home.js" in n for n in doc["required_failed"]), doc["required_failed"]
    finally:
        srv.shutdown()


def test_html_served_where_javascript_was_expected_fails():
    """An SPA fallback returning index.html for /home.js is a broken deploy."""
    srv, _ = make_server(assets={
        "/home.js": (200, b"<!doctype html><html>nope</html>", "text/html"),
        "/styles.css": (200, GOOD_CSS, "text/css")})
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1
        assert any("home.js" in n for n in doc["required_failed"])
    finally:
        srv.shutdown()


def test_an_empty_asset_fails():
    srv, _ = make_server(assets={
        "/home.js": (200, b"", "application/javascript"),
        "/styles.css": (200, GOOD_CSS, "text/css")})
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1
        assert any("home.js" in n for n in doc["required_failed"])
    finally:
        srv.shutdown()


def test_off_origin_assets_are_never_fetched():
    """A misconfigured or tampered page must not make this call the internet."""
    page = GOOD_INDEX.replace(
        b'<script src="/home.js"></script>',
        b'<script src="https://cdn.example.com/evil.js"></script>'
        b'<script src="/home.js"></script>')
    srv, seen = make_server(index=page)
    try:
        r, doc = run_check(srv)
        assert r.returncode == 0, r.stderr
        assert any("same-origin" in d for d in doc["degraded"]), doc["degraded"]
    finally:
        srv.shutdown()
    assert not any("example.com" in p for _, p in seen)


# == services ===========================================================

def test_an_unconfigured_optional_integration_degrades_but_does_not_fail():
    """An expired Gmail token is not a broken dashboard."""
    srv, _ = make_server(health=healthy({
        "gmail": {"key": "gmail", "status": "down", "detail": "no token"},
        "firefly": {"key": "firefly", "status": "down", "detail": "not connected"}}))
    try:
        r, doc = run_check(srv)
        assert r.returncode == 0, r.stderr
        assert doc["result"] == "pass", "an optional integration failed the deploy"
        assert set(doc["degraded"]) == {"service gmail", "service firefly"}
    finally:
        srv.shutdown()


def test_a_required_service_down_fails():
    srv, _ = make_server(health=healthy({
        "core": {"key": "core", "status": "down", "detail": "refused"}}))
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1
        assert "required services" in doc["required_failed"]
    finally:
        srv.shutdown()


def test_a_required_service_is_retried_within_the_budget():
    """Services may still be starting while the gateway already answers."""
    srv, _ = make_server(health_sequence=[
        healthy({"core": {"key": "core", "status": "starting"}}),
        healthy({"core": {"key": "core", "status": "starting"}}),
        healthy(),
    ])
    try:
        r, doc = run_check(srv)
        assert r.returncode == 0, r.stderr + json.dumps(doc)
        assert doc["result"] == "pass"
    finally:
        srv.shutdown()


@pytest.mark.parametrize("bad", ["a string", 42, ["list"], None])
def test_a_malformed_health_entry_is_structured_not_an_exception(bad):
    """It must produce a fail/degraded result, never an uncaught traceback."""
    srv, _ = make_server(health=healthy({"core": bad, "gmail": bad}))
    try:
        r, doc = run_check(srv)
        assert "Traceback" not in r.stderr, r.stderr
        assert r.returncode == 1
        assert doc["result"] == "fail"
        assert "required services" in doc["required_failed"]
        assert "service gmail" in doc["degraded"]
    finally:
        srv.shutdown()


@pytest.mark.parametrize("apps,why", [
    ([], "empty catalog"),
    ([{"name": "no key"}], "entry without a key"),
    ([{"key": "core"}], "entry without a name"),
    ({"not": "a list"}, "catalog is not a list"),
])
def test_a_broken_app_catalog_fails(apps, why):
    srv, _ = make_server(apps=apps)
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1, why
        assert "app catalog" in doc["required_failed"], why
    finally:
        srv.shutdown()


# == the boundaries =====================================================

def test_it_only_ever_issues_reads():
    """The entire network surface must be GET on the gateway's own reads."""
    srv, seen = make_server()
    try:
        run_check(srv)
    finally:
        srv.shutdown()
    assert seen, "the check made no requests at all"
    methods = {m for m, _ in seen}
    assert methods == {"GET"}, f"non-GET requests issued: {methods}"
    allowed = {"/", "/api/apps", "/api/health", "/home.js", "/styles.css"}
    touched = {p for _, p in seen}
    assert touched <= allowed, f"unexpected endpoints touched: {touched - allowed}"


def test_it_is_bounded_when_nothing_is_listening():
    """It must fail, not hang, or it wedges every deploy."""
    r = subprocess.run(
        ["bash", str(READINESS), "--json-only"], capture_output=True, text=True,
        timeout=90,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "FRANKENSTEIN_READINESS_URL": "http://127.0.0.1:1",
             "FRANKENSTEIN_READINESS_BUDGET": "3",
             "FRANKENSTEIN_READINESS_DELAY": "0.2",
             "FRANKENSTEIN_READINESS_TIMEOUT": "1"})
    assert r.returncode == 1
    assert json.loads(r.stdout)["result"] == "fail"


def test_the_whole_check_respects_one_shared_budget():
    """Retries across several stages must not multiply the deploy's wait."""
    import time
    srv, _ = make_server(health=healthy({"core": {"key": "core", "status": "down"}}))
    try:
        start = time.monotonic()
        r, _ = run_check(srv, FRANKENSTEIN_READINESS_BUDGET="3")
        elapsed = time.monotonic() - start
    finally:
        srv.shutdown()
    assert r.returncode == 1
    assert elapsed < 20, f"the budget was not respected: {elapsed:.1f}s"


def test_it_records_the_commit_it_checked():
    srv, _ = make_server()
    try:
        _, doc = run_check(srv)
        assert len(doc["checked_commit"]) == 40 or doc["checked_commit"] == "unknown"
        assert doc["at"].endswith("Z")
    finally:
        srv.shutdown()


def test_error_detail_is_scrubbed_of_credential_shapes():
    leak = "ghp_" + "B" * 36
    srv, _ = make_server(health=healthy({
        "gmail": {"key": "gmail", "status": "down",
                  "detail": f"auth failed with {leak}"}}))
    try:
        r, doc = run_check(srv)
        assert leak not in json.dumps(doc), "a token-shaped string was reported"
        assert leak not in r.stderr
    finally:
        srv.shutdown()


def test_it_never_mutates_or_deploys():
    """Assert on CODE, not prose -- the header comment legitimately explains
    what the check refuses to do."""
    src = "\n".join(l for l in READINESS.read_text().splitlines()
                    if not l.lstrip().startswith("#"))
    for forbidden in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"', "docker",
                      "promote", "rollback", "/sync", "git push", "urlopen(data"):
        assert forbidden not in src, f"readiness.sh references {forbidden}"
    assert 'method="GET"' in src
