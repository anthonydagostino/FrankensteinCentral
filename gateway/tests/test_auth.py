"""The front door asks who you are (SCRUM-98).

WHY THIS FILE EXISTS: the dashboard had no authentication anywhere. SCRUM-115
stopped a remote page from reading it and SCRUM-116 took every other service
off the network, which left exactly one path: a device already on the LAN,
pointed at the gateway. This is that door.

THE TWO PROPERTIES THAT MATTER, in order:

  1. With a password set, nothing that carries data answers without a session.
     Every /api/ route except the three deliberately public ones; and the
     session is a cookie WE signed, not one that merely looks like one.
  2. With NO password set, nothing changes and nothing is quiet about it.
     Fail-open is a considered call (the reasons are in auth.py); fail-open
     AND silent is the bug this repo keeps finding, so /api/auth/status must
     say `configured: false` and the shell must still load to show the banner.

Everything here drives the real app through the real middleware stack. The
predicate tests exist too, but a wrong wire between them and the gateway would
pass those and still leave the door open.
"""
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from conftest import load_service_module  # noqa: E402

gw = load_service_module("gateway_main", "gateway/app/main.py")
auth = sys.modules["gateway_main__pkg.auth"] if "gateway_main__pkg.auth" in sys.modules else gw.auth

PW = "correct-horse-battery-staple"


@pytest.fixture
def locked(monkeypatch):
    """A gateway with a password set and no session — the LAN stranger."""
    monkeypatch.setattr(auth, "PASSWORD", PW)
    return TestClient(gw.app, base_url="http://localhost")


@pytest.fixture
def open_gw(monkeypatch):
    """No password configured: today's behaviour, and it must say so."""
    monkeypatch.setattr(auth, "PASSWORD", "")
    return TestClient(gw.app, base_url="http://localhost")


def login(client, password=PW, nxt="/"):
    return client.post("/login", data={"password": password, "next": nxt},
                       follow_redirects=False)


# ── 1. locked: data does not answer ─────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/assistant/home",          # the payload with everything on it
    "/api/firefly/networth",
    "/api/gmail/messages",
    "/api/core/settings",
    "/api/vault/summary",
    "/api/nosuchapp/anything",      # gated BEFORE the proxy decides it is unknown
])
def test_api_data_needs_a_session(locked, path):
    r = locked.get(path)
    assert r.status_code == 401, path
    assert r.json() == {"error": "login required"}


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutations_need_a_session_too(locked, method):
    r = locked.request(method, "/api/core/capture")
    assert r.status_code == 401


def test_a_navigation_to_a_gated_page_is_sent_to_login(locked):
    """Not a 401 page: a person typed an address, so send them somewhere they
    can act. `next` carries where they were going."""
    r = locked.get("/some/deep/page", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/some/deep/page"


def test_the_public_surface_is_exactly_the_shell_and_two_probes(locked):
    """These are the things a LAN stranger CAN see. Each is here for a reason
    given in auth.py; adding one is a decision, and this list is where the
    decision gets reviewed."""
    for path in ("/", "/index.html", "/home.js", "/auth.js", "/home.css",
                 "/manifest.webmanifest", "/icon.svg", "/sw.js",
                 "/api/health", "/api/apps", "/api/auth/status", "/login"):
        r = locked.get(path, follow_redirects=False)
        assert r.status_code == 200, (path, r.status_code)


def test_the_status_probe_never_leaks_and_says_who_is_asking(locked):
    r = locked.get("/api/auth/status")
    body = r.json()
    assert body == {"configured": True, "authenticated": False}
    assert PW not in r.text


# ── the session itself ──────────────────────────────────────────────────────

def test_the_right_password_opens_the_door(locked):
    r = login(locked, nxt="/api/assistant/home")
    assert r.status_code == 303
    assert r.headers["location"] == "/api/assistant/home"
    assert auth.COOKIE in r.cookies
    c = r.headers["set-cookie"].lower()
    assert "httponly" in c and "samesite=lax" in c and "path=/" in c
    # Plain http on the LAN: a Secure cookie could never be set and login would
    # silently do nothing. It must only be Secure when the page is https.
    assert "secure" not in c

    locked.cookies.set(auth.COOKIE, r.cookies[auth.COOKIE])
    assert locked.get("/api/auth/status").json() == {"configured": True, "authenticated": True}


def test_a_secure_context_gets_a_secure_cookie(locked):
    r = locked.post("/login", data={"password": PW, "next": "/"},
                    headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    assert "secure" in r.headers["set-cookie"].lower()


@pytest.mark.parametrize("wrong", ["", "wrong", PW + "x", PW[:-1], PW.upper()])
def test_a_wrong_password_gets_nothing(locked, wrong, monkeypatch):
    monkeypatch.setattr(auth.time, "sleep", lambda s: None)   # the brake, not the test
    r = login(locked, password=wrong)
    assert r.status_code == 401
    assert auth.COOKIE not in r.cookies
    assert "not right" in r.text


def test_a_wrong_password_costs_a_second(locked, monkeypatch):
    """A floor under a bad password: invisible to a person, a long weekend for
    a LAN brute force. Pinned so a later 'why is the test slow' does not remove
    it without noticing what it was for."""
    slept = []
    monkeypatch.setattr(auth.time, "sleep", lambda s: slept.append(s))
    login(locked, password="nope")
    assert slept == [1.0]
    slept.clear()
    login(locked)
    assert slept == [], "the right password must not be delayed — that would leak which it was"


def test_the_comparison_is_constant_time():
    src = Path(auth.__file__).read_text()
    assert "hmac.compare_digest(presented.encode(), PASSWORD.encode())" in src
    assert "presented == PASSWORD" not in src


# ── the cookie must be OURS ─────────────────────────────────────────────────

def test_a_forged_cookie_is_refused(locked):
    exp = int(time.time()) + 3600
    for forged in (f"{exp}.AAAA", f"{exp}.", "not-a-cookie", "", f"{exp}"):
        locked.cookies.set(auth.COOKIE, forged)
        assert locked.get("/api/assistant/home").status_code == 401, forged


def test_a_cookie_signed_under_a_different_password_is_refused(locked, monkeypatch):
    """Changing GATEWAY_PASSWORD is the revoke mechanism: every session dies at
    once. Verified by minting under one password and presenting under another."""
    monkeypatch.setattr(auth, "PASSWORD", "old-password")
    stale = auth.issue_cookie()
    monkeypatch.setattr(auth, "PASSWORD", PW)
    locked.cookies.set(auth.COOKIE, stale)
    assert locked.get("/api/assistant/home").status_code == 401


def test_an_expired_cookie_is_refused(monkeypatch):
    # A password must be SET here: with none, cookie_is_valid returns False
    # for every input, and this would pass while proving nothing about expiry.
    monkeypatch.setattr(auth, "PASSWORD", PW)
    good = auth.issue_cookie()
    exp = int(good.split(".")[0])
    assert auth.cookie_is_valid(good, now=exp - 1) is True
    assert auth.cookie_is_valid(good, now=exp) is False
    assert auth.cookie_is_valid(good, now=exp + 1) is False


def test_tampering_with_the_expiry_breaks_the_signature(monkeypatch):
    monkeypatch.setattr(auth, "PASSWORD", PW)
    good = auth.issue_cookie()
    assert auth.cookie_is_valid(good) is True, "baseline: the untampered cookie is accepted"
    exp, mac = good.split(".")
    longer = f"{int(exp) + 86400 * 365}.{mac}"
    assert auth.cookie_is_valid(longer) is False


def test_logout_clears_the_session(locked):
    r = login(locked)
    locked.cookies.set(auth.COOKIE, r.cookies[auth.COOKIE])
    assert locked.get("/api/auth/status").json()["authenticated"] is True
    r = locked.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    # Server told the browser to drop it; a browser that did so is logged out.
    assert 'fc_session=""' in r.headers["set-cookie"] or "max-age=0" in r.headers["set-cookie"].lower()


# ── no open redirect through /login ─────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "//evil.com", "//evil.com/x", "http://evil.com", "https://evil.com",
    "javascript:alert(1)", "/\\evil.com", "\\\\evil.com", "", None, "evil.com",
])
def test_next_cannot_leave_the_origin(bad):
    assert auth.safe_next(bad) == "/"


@pytest.mark.parametrize("good", ["/", "/notes/page.html", "/api/x?y=1", "/a/b/c"])
def test_next_keeps_a_same_origin_path(good):
    assert auth.safe_next(good) == good


def test_login_form_carries_next_through_escaped(locked):
    r = locked.get('/login?next=/x"><script>alert(1)</script>')
    assert r.status_code == 200
    assert "<script>alert" not in r.text
    assert "&lt;script&gt;" in r.text or "&quot;" in r.text


# ── 2. open: unchanged, and loud about it ───────────────────────────────────

def test_with_no_password_nothing_is_gated(open_gw, monkeypatch):
    """Today's behaviour, exactly. The reasoning for fail-OPEN is in auth.py;
    what is NOT acceptable is for this state to be quiet — see the next test."""
    class Up:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, *a, **k):
            class R: status_code = 200; content = b"{}"; headers = {"content-type": "application/json"}
            return R()
    monkeypatch.setattr(gw.httpx, "AsyncClient", Up)
    assert open_gw.get("/api/assistant/home").status_code == 200
    assert open_gw.get("/some/page", follow_redirects=False).status_code != 303


def test_with_no_password_the_status_probe_says_so(open_gw):
    """The banner on the page, verify.sh and frankenstein-status.sh all read
    this. `configured: false` is the whole signal."""
    assert open_gw.get("/api/auth/status").json() == {"configured": False, "authenticated": True}


def test_with_no_password_login_pages_do_not_pretend(open_gw):
    """A login form with nothing to check against is a lie. Send them home."""
    assert open_gw.get("/login", follow_redirects=False).headers["location"] == "/"
    assert open_gw.post("/login", data={"password": "x"}, follow_redirects=False).headers["location"] == "/"


# ── the guard is in front of the proxy, not beside it ───────────────────────

def test_a_refused_request_never_reaches_upstream(locked, monkeypatch):
    reached = []
    class Upstream:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, *a, **k):
            reached.append(a); raise RuntimeError("must not be called")
    monkeypatch.setattr(gw.httpx, "AsyncClient", Upstream)
    assert locked.get("/api/gmail/messages").status_code == 401
    assert reached == [], "the proxy fetched data for a caller with no session"


def test_the_shell_stays_public_so_the_worker_does_not_cache_the_login_page(locked):
    """Gating index.html would make the service worker precache /login under
    '/', and the offline hub would open onto a form. The shell is code; the
    data behind it is what is gated."""
    r = locked.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "<title>FrankensteinCentral" in r.text
