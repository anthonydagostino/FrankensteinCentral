"""Tests for the post-deploy readiness check (scripts/readiness.sh).

Started containers are not a working application. This check answers "is the
core dashboard actually serving?" and it must do so:

  * READ-ONLY -- GET only, and never a sync/email/calendar/financial endpoint
  * BOUNDED   -- one shared time budget; it can never hang a deploy
  * HONEST    -- an unconfigured third-party integration is DEGRADED, and must
                 never fail a core deployment

A 200 is not the hub. The Product Owner reproduced a PASS for
``<html><body>Application unavailable</body></html>``: the old check asked only
for "<html" and the substring "app", which that error page satisfies, and it
never requested a single static asset. The entry page must now carry the
dashboard's own structure and its declared same-origin JS/CSS must load.
"""
import json
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "scripts" / "readiness.sh"
STATIC = ROOT / "gateway" / "static"

# The markers readiness.sh looks for, kept in step with the script's default.
MARKERS = ["cc-grid", "cc-donext", "cc-money"]

# A minimal page that is unmistakably the hub: the structure markers plus the
# same-origin assets a real entry page declares.
ESSENTIAL = ["/app.js", "/home.js", "/styles.css", "/home.css"]

INDEX = (
    b"<!doctype html><html><head>"
    b"<link rel='stylesheet' href='/styles.css'>"
    b"<link rel='stylesheet' href='/home.css'>"
    b"</head><body>"
    b"<main id='cc-grid'><section id='cc-donext'></section>"
    b"<section id='cc-money'></section></main>"
    b"<script src='/app.js'></script>"
    b"<script src='/home.js'></script>"
    b"</body></html>"
)
APP_JS = b"const hub = 1;\n"
STYLES = b".cc-grid { display: grid; }\n"
ASSETS = {"/app.js": APP_JS, "/home.js": APP_JS,
          "/styles.css": STYLES, "/home.css": STYLES}

REQUIRED = ["core", "tasks"]
OPTIONAL = ["gmail", "firefly"]


def make_server(index=INDEX, apps=None, health=None, status_for=None,
                assets=None, ctype_for=None):
    """A stub gateway that records every request it receives."""
    seen = []
    if apps is None:
        apps = [{"key": "core", "name": "Core"}, {"key": "tasks", "name": "Tasks"}]
    if health is None:
        health = {k: {"key": k, "status": "up"} for k in REQUIRED + OPTIONAL}
    files = dict(ASSETS)
    if assets is not None:
        files = assets
    types = {p: ("text/javascript" if p.endswith(".js") else "text/css")
             for p in files}
    types.update(ctype_for or {})

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
                self._send(code, json.dumps(health).encode())
            elif self.path in files:
                self._send(code, files[self.path],
                           types.get(self.path, "application/octet-stream"))
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
        "FRANKENSTEIN_READINESS_RETRIES": "2",
        "FRANKENSTEIN_READINESS_DELAY": "0",
        "FRANKENSTEIN_READINESS_TIMEOUT": "2",
        "FRANKENSTEIN_REQUIRED_SERVICES": " ".join(REQUIRED),
        "FRANKENSTEIN_OPTIONAL_SERVICES": " ".join(OPTIONAL),
    }
    env.update(env_over)
    r = subprocess.run(["bash", str(READINESS), "--json-only"],
                       capture_output=True, text=True, timeout=120, env=env)
    return r, json.loads(r.stdout)


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


def test_an_unconfigured_optional_integration_degrades_but_does_not_fail():
    """Anthony's Gmail token expiring is not a broken dashboard."""
    health = {k: {"key": k, "status": "up"} for k in REQUIRED}
    health["gmail"] = {"key": "gmail", "status": "down", "detail": "no token"}
    health["firefly"] = {"key": "firefly", "status": "down", "detail": "not connected"}
    srv, _ = make_server(health=health)
    try:
        r, doc = run_check(srv)
        assert r.returncode == 0, r.stderr
        assert doc["result"] == "pass", "an optional integration failed the deploy"
        assert set(doc["degraded"]) == {"service gmail", "service firefly"}
    finally:
        srv.shutdown()


def test_a_required_service_down_fails():
    health = {k: {"key": k, "status": "up"} for k in REQUIRED + OPTIONAL}
    health["core"] = {"key": "core", "status": "down", "detail": "connection refused"}
    srv, _ = make_server(health=health)
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1
        assert doc["result"] == "fail"
        assert "service core" in doc["required_failed"]
    finally:
        srv.shutdown()


@pytest.mark.parametrize("apps,why", [
    ([], "empty catalog"),
    ([{"name": "no key"}], "entry without a key"),
    ([{"key": "core"}], "entry without a name"),
    ({"not": "a list"}, "catalog is not a list"),
])
def test_a_broken_app_catalog_fails(apps, why):
    """Every dashboard page renders from this; a 200 is not enough."""
    srv, _ = make_server(apps=apps)
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1, why
        assert "app catalog" in doc["required_failed"], why
    finally:
        srv.shutdown()


# ── the false PASS the Product Owner reproduced ─────────────────────────────

@pytest.mark.parametrize("index,why", [
    (b"upstream unavailable", "a bare proxy error string"),
    (b"<html><body>Application unavailable</body></html>",
     "a 200 error page containing 'Application'"),
    (b"<!doctype html><html><head><title>502 Bad Gateway</title></head>"
     b"<body><h1>Application error</h1><p>The app is down.</p></body></html>",
     "a styled 200 error page mentioning 'app'"),
])
def test_a_200_that_is_not_the_dashboard_fails(index, why):
    """A proxy error page can still return 200, and can still contain 'app'."""
    srv, _ = make_server(index=index)
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1, why
        assert "gateway serves the dashboard" in doc["required_failed"], why
    finally:
        srv.shutdown()


def test_an_entry_page_that_stops_declaring_its_script_fails():
    """Codex FC-002 review of 5c5d64f, finding 2. Checking only the assets a
    page happens to declare cannot catch a page that declares none: remove the
    script tag, keep the stylesheet and healthy APIs, and there is nothing left
    to fail on. The essential set is required by name, not by inference."""
    index = (b"<!doctype html><html><head>"
             b"<link rel='stylesheet' href='/styles.css'>"
             b"<link rel='stylesheet' href='/home.css'>"
             b"</head><body><main id='cc-grid'><div id='cc-donext'></div>"
             b"<div id='cc-money'></div></main></body></html>")
    srv, _ = make_server(index=index)
    try:
        r, doc = run_check(srv)
    finally:
        srv.shutdown()
    assert r.returncode == 1, "a dashboard with no JavaScript at all passed"
    assert "entry page declares its essential assets" in doc["required_failed"]


@pytest.mark.parametrize("dropped", ESSENTIAL)
def test_dropping_any_single_essential_asset_fails(dropped):
    """Each named asset is load-bearing, proven one at a time."""
    index = INDEX.replace(f"<script src='{dropped}'></script>".encode(), b"")
    index = index.replace(
        f"<link rel='stylesheet' href='{dropped}'>".encode(), b"")
    assert index != INDEX, f"{dropped} is not in the fixture page"
    srv, _ = make_server(index=index)
    try:
        r, doc = run_check(srv)
    finally:
        srv.shutdown()
    assert r.returncode == 1, f"dropping {dropped} still passed"
    assert "entry page declares its essential assets" in doc["required_failed"]


def test_a_cache_busted_essential_asset_still_counts_as_declared():
    """A query string is the same file, and must not read as a missing one."""
    index = INDEX.replace(b"/app.js'", b"/app.js?v=3'")
    srv, seen = make_server(index=index)
    try:
        r, doc = run_check(srv, FRANKENSTEIN_READINESS_RETRIES="1")
    finally:
        srv.shutdown()
    assert "entry page declares its essential assets" not in doc["required_failed"]
    assert any(p.startswith("/app.js?") for _, p in seen)


def test_every_essential_asset_is_referenced_by_the_real_index():
    src = READINESS.read_text()
    declared = re.search(r'FRANKENSTEIN_READINESS_ASSETS:-([^}]*)\}', src)
    assert declared, "readiness.sh no longer declares an essential asset list"
    index = (STATIC / "index.html").read_text()
    for asset in declared.group(1).split():
        assert asset in index, \
            f"essential asset {asset} is not referenced by gateway/static/index.html"


def test_a_page_with_markers_but_no_assets_to_check_fails():
    """Structure alone is cheap to forge; something must actually load."""
    index = (b"<!doctype html><html><body><main id='cc-grid'>"
             b"<div id='cc-donext'></div><div id='cc-money'></div>"
             b"</main></body></html>")
    srv, _ = make_server(index=index)
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1
        assert "entry page assets" in doc["required_failed"]
    finally:
        srv.shutdown()


def test_a_missing_js_asset_fails():
    """A hub whose script 404s is a blank screen, however green the containers."""
    srv, _ = make_server(assets={k: v for k, v in ASSETS.items()
                                 if k != "/app.js"})
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1
        assert "asset /app.js" in doc["required_failed"]
    finally:
        srv.shutdown()


def test_html_served_in_place_of_js_fails():
    """The classic SPA fallback: every unknown path answers with index.html."""
    srv, _ = make_server(assets=dict(ASSETS, **{"/app.js": INDEX}),
                         ctype_for={"/app.js": "text/html"})
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1
        assert "asset /app.js" in doc["required_failed"]
    finally:
        srv.shutdown()


def test_wrong_content_type_for_js_fails():
    srv, _ = make_server(ctype_for={"/app.js": "text/plain"})
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1
        assert "asset /app.js" in doc["required_failed"]
    finally:
        srv.shutdown()


def test_an_empty_asset_fails():
    srv, _ = make_server(assets=dict(ASSETS, **{"/app.js": b"   \n"}))
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1
        assert "asset /app.js" in doc["required_failed"]
    finally:
        srv.shutdown()


def test_external_asset_urls_are_never_requested():
    """A page that names another host must not aim this check at it."""
    index = (b"<!doctype html><html><head>"
             b"<link rel='stylesheet' href='https://evil.example/x.css'>"
             b"<link rel='stylesheet' href='//evil.example/y.css'>"
             b"</head><body><main id='cc-grid'><div id='cc-donext'></div>"
             b"<div id='cc-money'></div></main>"
             b"<script src='https://evil.example/z.js'></script>"
             b"<script src='/app.js'></script><script src='/home.js'></script>"
             b"<link rel='stylesheet' href='/styles.css'>"
             b"<link rel='stylesheet' href='/home.css'>"
             b"</body></html>")
    srv, seen = make_server(index=index)
    try:
        r, doc = run_check(srv)
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert not any("evil.example" in p for _, p in seen)
    assert "evil.example" not in json.dumps(doc)


# ── the genuine hub, served from the real files in this repository ──────────

def test_the_real_dashboard_passes_its_own_check():
    """Pins the marker list and the asset rules to gateway/static/index.html.

    If a marker is renamed out of the page, or the entry page stops declaring
    the assets, this fails in CI rather than failing every deploy on the box.
    """
    index = (STATIC / "index.html").read_bytes()
    assets, types = {}, {}
    for f in STATIC.iterdir():
        if f.suffix in (".js", ".css"):
            assets["/" + f.name] = f.read_bytes()
            types["/" + f.name] = ("text/javascript" if f.suffix == ".js"
                                   else "text/css")
    srv, seen = make_server(index=index, assets=assets, ctype_for=types)
    try:
        r, doc = run_check(srv)
    finally:
        srv.shutdown()
    assert r.returncode == 0, (r.stderr, doc["required_failed"])
    assert doc["result"] == "pass"
    fetched = {p for _, p in seen}
    assert "/app.js" in fetched and "/home.js" in fetched, \
        "the real entry page's scripts were not verified"
    assert any(p.endswith(".css") for p in fetched), \
        "the real entry page's stylesheets were not verified"


def test_every_marker_is_present_in_the_real_index():
    index = (STATIC / "index.html").read_text()
    src = READINESS.read_text()
    declared = re.search(r'FRANKENSTEIN_READINESS_MARKERS:-([^}]*)\}', src)
    assert declared, "readiness.sh no longer declares a default marker list"
    for marker in declared.group(1).split():
        assert marker in index, f"marker {marker} is not in gateway/static/index.html"


# ── malformed health must be a result, not a crash ─────────────────────────

@pytest.mark.parametrize("entry", [
    "up",                       # a bare string where an object was expected
    ["up"],                     # a list
    42,                         # a number
    {"status": None},           # present, but not a string
    {"status": ["up"]},         # a list status
    {"status": "up", "detail": {"nested": "object"}},   # non-string detail
])
def test_a_malformed_required_health_entry_is_a_structured_failure(entry):
    """It must never come back as an uncaught traceback with no JSON at all."""
    health = {k: {"key": k, "status": "up"} for k in REQUIRED + OPTIONAL}
    health["core"] = entry
    srv, _ = make_server(health=health)
    try:
        r, doc = run_check(srv)
    finally:
        srv.shutdown()
    if entry == {"status": "up", "detail": {"nested": "object"}}:
        # A malformed *detail* on an otherwise up service is not a failure.
        assert doc["result"] == "pass", r.stderr
        return
    assert r.returncode == 1, r.stderr
    assert doc["result"] == "fail"
    assert "service core" in doc["required_failed"]
    assert "Traceback" not in r.stderr


@pytest.mark.parametrize("entry", ["down", ["x"], 0, {"status": None}])
def test_a_malformed_optional_health_entry_degrades_and_never_fails(entry):
    health = {k: {"key": k, "status": "up"} for k in REQUIRED + OPTIONAL}
    health["gmail"] = entry
    srv, _ = make_server(health=health)
    try:
        r, doc = run_check(srv)
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert doc["result"] == "pass"
    assert "service gmail" in doc["degraded"]


def test_health_that_is_not_an_object_fails_without_crashing():
    srv, _ = make_server(health=["core", "tasks"])
    try:
        r, doc = run_check(srv)
    finally:
        srv.shutdown()
    assert r.returncode == 1
    assert "service health endpoint" in doc["required_failed"]
    assert "Traceback" not in r.stderr


# ── bounded, read-only, honest ─────────────────────────────────────────────

def test_a_required_service_that_comes_up_late_is_retried():
    """A service still starting when the HTML is already served is ordinary."""
    state = {"n": 0}
    health_up = {k: {"key": k, "status": "up"} for k in REQUIRED + OPTIONAL}
    health_down = dict(health_up, core={"key": "core", "status": "starting"})

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                self._send(200, INDEX, "text/html")
            elif self.path in ASSETS:
                self._send(200, ASSETS[self.path],
                           "text/javascript" if self.path.endswith(".js")
                           else "text/css")
            elif self.path == "/api/apps":
                self._send(200, json.dumps(
                    [{"key": "core", "name": "Core"}]).encode(),
                    "application/json")
            elif self.path == "/api/health":
                state["n"] += 1
                body = health_up if state["n"] > 1 else health_down
                self._send(200, json.dumps(body).encode(), "application/json")
            else:
                self._send(404, b"nope", "text/plain")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r, doc = run_check(srv, FRANKENSTEIN_READINESS_RETRIES="3")
    finally:
        srv.shutdown()
    assert state["n"] > 1, "health was requested only once; it was not retried"
    assert r.returncode == 0, r.stderr
    assert doc["result"] == "pass"


def test_it_only_ever_issues_reads():
    """The entire network surface must be GET on known read-only endpoints."""
    srv, seen = make_server()
    try:
        run_check(srv)
    finally:
        srv.shutdown()
    assert seen, "the check made no requests at all"
    methods = {m for m, _ in seen}
    assert methods == {"GET"}, f"non-GET requests issued: {methods}"
    allowed = {"/", "/api/apps", "/api/health"} | set(ESSENTIAL)
    touched = {p for _, p in seen}
    assert touched <= allowed, f"unexpected endpoints touched: {touched - allowed}"


def test_it_is_bounded_when_nothing_is_listening():
    """It must fail, not hang, or it wedges every deploy."""
    r = subprocess.run(
        ["bash", str(READINESS), "--json-only"], capture_output=True, text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "FRANKENSTEIN_READINESS_URL": "http://127.0.0.1:1",
             "FRANKENSTEIN_READINESS_RETRIES": "2",
             "FRANKENSTEIN_READINESS_DELAY": "0",
             "FRANKENSTEIN_READINESS_TIMEOUT": "1"})
    assert r.returncode == 1
    assert json.loads(r.stdout)["result"] == "fail"


def test_the_whole_check_shares_one_time_budget():
    """Retrying the page and then retrying health must not add up past it."""
    import time
    start = time.monotonic()
    r = subprocess.run(
        ["bash", str(READINESS), "--json-only"], capture_output=True, text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "FRANKENSTEIN_READINESS_URL": "http://127.0.0.1:1",
             "FRANKENSTEIN_READINESS_RETRIES": "4",
             "FRANKENSTEIN_READINESS_DELAY": "1",
             "FRANKENSTEIN_READINESS_TIMEOUT": "1"})
    elapsed = time.monotonic() - start
    doc = json.loads(r.stdout)
    assert doc["result"] == "fail"
    # budget = 4 * (1 + 1) + 1 = 9s. Generous slack for process startup.
    assert elapsed < doc["budget_seconds"] + 15, \
        f"took {elapsed:.1f}s against a {doc['budget_seconds']}s budget"


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
    health = {k: {"key": k, "status": "up"} for k in REQUIRED}
    health["gmail"] = {"key": "gmail", "status": "down",
                       "detail": f"auth failed with {leak}"}
    health["firefly"] = {"key": "firefly", "status": "up"}
    srv, _ = make_server(health=health)
    try:
        r, doc = run_check(srv)
        assert leak not in json.dumps(doc), "a token-shaped string was reported"
        assert leak not in r.stderr
    finally:
        srv.shutdown()


def test_it_never_mutates_or_deploys():
    """Assert on CODE, not prose -- the header comment legitimately explains
    what the check refuses to do, and matching that would be meaningless."""
    src = "\n".join(l for l in READINESS.read_text().splitlines()
                     if not l.lstrip().startswith("#"))
    for forbidden in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"', "docker",
                      "promote", "rollback", "/sync", "git push", "urlopen(data"):
        assert forbidden not in src, f"readiness.sh references {forbidden}"
    # The one HTTP call site must be explicitly GET.
    assert 'method="GET"' in src
