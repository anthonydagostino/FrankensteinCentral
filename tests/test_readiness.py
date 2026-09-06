"""Tests for the post-deploy readiness check (scripts/readiness.sh).

Started containers are not a working application. This check answers "is the
core dashboard actually serving?" and it must do so:

  * READ-ONLY -- GET only, and never a sync/email/calendar/financial endpoint
  * BOUNDED   -- finite retries and timeouts; it can never hang a deploy
  * HONEST    -- an unconfigured third-party integration is DEGRADED, and must
                 never fail a core deployment
"""
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "scripts" / "readiness.sh"

INDEX = b"<!doctype html><html><body><div id='apps'>hub</div></body></html>"

REQUIRED = ["core", "tasks"]
OPTIONAL = ["gmail", "firefly"]


def make_server(index=INDEX, apps=None, health=None, status_for=None):
    """A stub gateway that records every request it receives."""
    seen = []
    if apps is None:
        apps = [{"key": "core", "name": "Core"}, {"key": "tasks", "name": "Tasks"}]
    if health is None:
        health = {k: {"key": k, "status": "up"} for k in REQUIRED + OPTIONAL}

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
            else:
                self._send(404, b"{}")

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


def test_a_200_that_is_not_the_dashboard_fails():
    """A proxy error page can still return 200."""
    srv, _ = make_server(index=b"upstream unavailable")
    try:
        r, doc = run_check(srv)
        assert r.returncode == 1
        assert "gateway serves the dashboard" in doc["required_failed"]
    finally:
        srv.shutdown()


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
    allowed = {"/", "/api/apps", "/api/health"}
    assert {p for _, p in seen} <= allowed, \
        f"unexpected endpoints touched: {{p for _, p in seen}} - {allowed}"


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
