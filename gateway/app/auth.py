"""Single-user login for the front door (SCRUM-98).

WHY. The dashboard had no authentication anywhere. `docs/AUDIT.md` called that
"acceptable for home" when the page showed container health. It now shows net
worth, account balances, spending, password-health metadata and the inbox, and
a move is coming — new networks, and eventually a guest on one of them.
SCRUM-115 stopped a remote page from reading it; SCRUM-116 took every other
service off the network. This is the last door: a device that is already on the
LAN, pointed at the gateway.

WHAT IT IS. One password, one signed cookie, no dependency. The session cookie
is `<expiry>.<hmac>` where the HMAC key is derived from the password, compared
in constant time. There is no user table because there is no second user.

UNSET MEANS OFF, LOUDLY. This is the opposite call from SCRUM-114, on purpose.
That route was machine-to-machine, and failing closed cost nothing a human
would notice. This is the owner's own front door on a phone: failing closed on
a missing variable would present a login for a password that was never set,
with no way forward short of SSH. So with GATEWAY_PASSWORD empty nothing is
gated — exactly today's behaviour — and the fact is reported everywhere a
person might look: /api/auth/status, frankenstein-status.sh, verify.sh, and a
banner on the dashboard itself. Insecure-by-default is a hole; insecure-and-
silent is the hole this repo keeps finding. The second one is fixed here.

WHAT STAYS PUBLIC, AND WHY. The static shell (index.html, the scripts, the
stylesheets, the manifest) is code, not data: it renders nothing without
/api/assistant/home, which is gated. Gating the shell would also make the
service worker precache the login page under "/". /api/health and /api/apps
stay open because scripts/verify.sh probes them from the box and they expose
only the service catalog and up/down. NOT exempted by client IP: from inside
the container the host's 127.0.0.1 arrives as the docker bridge address, and a
loopback rule that only works in tests is worse than an honest path list.

WHAT THIS IS NOT. Binding to Tailscale — the other half of SCRUM-98 — depends
on SCRUM-48 and is not code in this repo. The acceptance signal on the ticket
("unreachable from a device that isn't yours, tested from one") needs a human
with two devices and is not claimed here.
"""
import base64
import hashlib
import hmac
import html
import os
import secrets
import time
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

PASSWORD = os.environ.get("GATEWAY_PASSWORD", "").strip()
COOKIE = "fc_session"
SESSION_DAYS = int(os.environ.get("GATEWAY_SESSION_DAYS", "30") or 30)

# Paths reachable without a session. Prefix match on directories, exact match
# otherwise. Adding to this list is a decision to expose something to the LAN.
PUBLIC_EXACT = {"/", "/index.html", "/login", "/logout", "/api/auth/status",
                "/api/health", "/api/apps", "/manifest.webmanifest", "/icon.svg",
                "/sw.js", "/jobs.html", "/lounge.html"}
PUBLIC_SUFFIXES = (".js", ".css", ".svg", ".webmanifest", ".png", ".ico")

router = APIRouter()


def enabled() -> bool:
    return bool(PASSWORD)


def _key() -> bytes:
    # Derived, not stored: a restart keeps every session valid, and rotating the
    # password invalidates every session at once — which is what you want.
    return hashlib.sha256(b"fc-session-v1:" + PASSWORD.encode()).digest()


def _sign(expiry: int) -> str:
    msg = str(expiry).encode()
    mac = hmac.new(_key(), msg, hashlib.sha256).digest()
    return f"{expiry}." + base64.urlsafe_b64encode(mac).decode().rstrip("=")


def issue_cookie() -> str:
    return _sign(int(time.time()) + SESSION_DAYS * 86400)


def cookie_is_valid(value: str | None, now: float | None = None) -> bool:
    """True for a cookie we signed that has not expired.

    Constant-time on the MAC. Cheap to be wrong about: `==` leaks the signature
    one byte at a time to anyone who can time the response.
    """
    if not value or not enabled():
        return False
    expiry_s, _, _mac = value.partition(".")
    if not expiry_s.isdigit():
        return False
    expiry = int(expiry_s)
    if expiry <= (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(_sign(expiry), value)


def is_public(path: str) -> bool:
    if path in PUBLIC_EXACT:
        return True
    if path.startswith("/api/"):
        return False           # every API route is gated unless listed above
    return path.endswith(PUBLIC_SUFFIXES)


def safe_next(raw: str | None) -> str:
    """A same-origin path to return to after login, or "/".

    An open redirect here would turn the login page into a phishing hop, so:
    must start with exactly one slash (`//evil.com` is scheme-relative), no
    scheme, no backslash (browsers normalise `\\` to `/`).
    """
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    if ":" in raw.split("?", 1)[0] or "\\" in raw:
        return "/"
    return raw


def _secure(request: Request) -> bool:
    # Only mark the cookie Secure when the page is actually https, or the LAN
    # (plain http) could never set it and login would silently do nothing.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto == "https"


# --- the middleware body, called from main.py --------------------------------

async def guard(request: Request, call_next):
    if not enabled() or is_public(request.url.path):
        return await call_next(request)
    if cookie_is_valid(request.cookies.get(COOKIE)):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        # A fetch() from the page. auth.js turns this into a trip to /login.
        return JSONResponse({"error": "login required"}, status_code=401,
                            headers={"WWW-Authenticate": "Cookie"})
    target = "/login?next=" + quote(request.url.path, safe="/")
    return RedirectResponse(target, status_code=303)


# --- routes --------------------------------------------------------------------

PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in — FrankensteinCentral</title>
<style>
 body{{background:#0d0f14;color:#e7ecf3;font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
      margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}}
 main{{width:100%;max-width:360px}}
 h1{{font-size:20px;margin:0 0 14px}}
 input{{width:100%;box-sizing:border-box;font:inherit;background:#161a23;color:#e7ecf3;
        border:1px solid #262c3a;border-radius:9px;padding:11px 12px;margin:6px 0 12px}}
 button{{width:100%;font:inherit;font-weight:700;background:#7c9cff;color:#0d0f14;border:0;
         border-radius:9px;padding:11px;cursor:pointer}}
 .err{{color:#ff7a7a;font-size:13px;margin:0 0 10px}}
 .muted{{color:#8a93a6;font-size:12.5px;margin-top:14px}}
</style>
<main>
 <h1>FrankensteinCentral</h1>
 {error}
 <form method="post" action="/login" autocomplete="on">
  <input type="hidden" name="next" value="{next}">
  <input type="password" name="password" placeholder="Password" autofocus
         autocomplete="current-password" required>
  <button type="submit">Sign in</button>
 </form>
 <p class="muted">Single-user hub. The password is <code>GATEWAY_PASSWORD</code> in
 the box's <code>.env</code>.</p>
</main>"""


def _page(next_path: str, error: str = "") -> HTMLResponse:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return HTMLResponse(PAGE.format(next=html.escape(next_path), error=err),
                        status_code=401 if error else 200)


@router.get("/login")
async def login_form(request: Request):
    if not enabled():
        return RedirectResponse("/", status_code=303)
    nxt = safe_next(request.query_params.get("next"))
    if cookie_is_valid(request.cookies.get(COOKIE)):
        return RedirectResponse(nxt, status_code=303)
    return _page(nxt)


@router.post("/login")
async def login_submit(request: Request):
    if not enabled():
        return RedirectResponse("/", status_code=303)
    # Parsed by hand: Starlette's request.form() needs python-multipart, which
    # the gateway does not ship, and a login that 500s on submit is a lockout.
    # Two urlencoded fields do not justify a dependency.
    form = parse_qs((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)
    presented = (form.get("password") or [""])[0]
    nxt = safe_next((form.get("next") or [""])[0])
    if not hmac.compare_digest(presented.encode(), PASSWORD.encode()):
        # One second is invisible to a person and turns a LAN brute force into a
        # long weekend. Not a substitute for a good password; a floor under it.
        time.sleep(1.0)
        return _page(nxt, "That password is not right.")
    resp = RedirectResponse(nxt, status_code=303)
    resp.set_cookie(COOKIE, issue_cookie(), max_age=SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=_secure(request), path="/")
    return resp


@router.post("/logout")
async def logout(request: Request):
    resp = RedirectResponse("/login" if enabled() else "/", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.get("/api/auth/status")
async def status(request: Request):
    """Public on purpose: it is how the dashboard knows to show the banner, and
    how verify.sh and frankenstein-status.sh report the gap. Says whether a
    password is SET and whether THIS caller holds a session. Never the value."""
    return {"configured": enabled(),
            "authenticated": (not enabled()) or cookie_is_valid(request.cookies.get(COOKIE))}


def new_password_hint() -> str:
    """For deploy output and docs: a way to mint one without openssl."""
    return secrets.token_urlsafe(18)
