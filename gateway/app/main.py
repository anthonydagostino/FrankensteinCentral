import asyncio
import ipaddress
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .registry import load_registry

app = FastAPI(title="FrankensteinCentral Gateway")
REGISTRY = {s.key: s for s in load_registry()}
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.include_router(auth.router)

# --- Host allowlist (SCRUM-115) ----------------------------------------------
#
# DNS REBINDING, and why an unauthenticated LAN service needs this even though
# "it is only on my network". A page you visit — an ad iframe will do — can
# re-resolve its OWN domain to this box's private address a moment after it
# loads. The browser still believes it is talking to that domain, so the
# request is SAME-ORIGIN, and same-origin means the attacker's script can READ
# THE RESPONSE. The exposure is therefore not "devices on the LAN"; it is any
# page in any browser on any device on the LAN.
#
# SCRUM-114 shut the two doors to /internal/token. This shuts the hallway: the
# same attack reads net worth, the inbox, the calendar and every other
# unauthenticated endpoint the gateway fronts.
#
# The defence is one header. In a rebinding attack the browser sends the
# ATTACKER'S DOMAIN in `Host` — that is what it navigated to — while the packet
# goes to this box. It cannot forge a Host it did not navigate to. So: accept
# IP literals and localhost, refuse unknown names, and rebinding stops working.
#
# Why IP literals are accepted wholesale rather than pinned: the box's LAN
# address is DHCP-assigned and not knowable from the repo, and a browser cannot
# be made to send an IP literal it did not navigate to. Reaching the box by its
# raw address already requires being on the network and knowing it — that is
# SCRUM-98's problem (bind to Tailscale, add auth), not this one. This ticket
# closes the remote path, not the local one.
#
# Names this box answers to (a Tailscale name, a hosts-file alias) go in
# GATEWAY_ALLOWED_HOSTS, comma-separated.
ALLOWED_HOSTS = [
    h.strip().lower()
    for h in os.environ.get("GATEWAY_ALLOWED_HOSTS", "").split(",")
    if h.strip()
]


def host_is_allowed(host_header: str) -> bool:
    """True when `Host` names something this box legitimately answers to."""
    # A duplicated Host header arrives comma-joined. Judge the first, because
    # that is the one an intermediary would act on.
    host = host_header.split(",")[0].strip().lower()
    if host.startswith("["):                      # [::1]:8080
        host = host[1:host.index("]")] if "]" in host else host[1:]
    elif host.count(":") == 1:                    # 192.168.1.50:8080
        host = host.rsplit(":", 1)[0]
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    return host in ALLOWED_HOSTS


@app.get("/api/apps")
async def list_apps():
    """The catalog every dashboard renders from."""
    return [
        {
            "key": s.key,
            "name": s.name,
            "description": s.description,
            "icon": s.icon,
        }
        for s in REGISTRY.values()
    ]


async def _probe(client: httpx.AsyncClient, key: str, url: str) -> dict:
    try:
        r = await client.get(f"{url}/health", timeout=3)
        r.raise_for_status()
        return {"key": key, "status": "up", "detail": r.json()}
    except Exception as exc:  # noqa: BLE001 - report any failure as down
        return {"key": key, "status": "down", "detail": str(exc)}


@app.get("/api/health")
async def aggregate_health():
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_probe(client, s.key, s.url) for s in REGISTRY.values())
        )
    return {r["key"]: r for r in results}


@app.api_route(
    "/api/{app_key}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy(app_key: str, path: str, request: Request):
    """Reverse-proxy /api/<app>/<path> to the matching sub-app service."""
    # SCRUM-114. The proxy forwards ANY path to ANY registered service with no
    # authentication, and gmail exposes /internal/token, which hands out a live
    # OAuth access token carrying gmail.modify AND calendar.events. So
    # `GET /api/gmail/internal/token` let any unauthenticated caller on the
    # network read and modify the whole inbox and read and write the calendar.
    #
    # The docstring on that route said "internal docker network only". Nothing
    # enforced it: `/internal/` was a naming convention and the proxy had never
    # heard of it.
    #
    # 404, not 403: a 403 confirms the route exists and is worth attacking. To
    # anything outside, an internal route is indistinguishable from a route
    # that was never written. Matched per SEGMENT, so it cannot be slipped past
    # with `x-internal` or `internalish`, and case-folded because the path
    # arrives from the caller.
    if any(seg.lower() == "internal" for seg in path.split("/")):
        return JSONResponse({"error": "not found"}, status_code=404)

    sub = REGISTRY.get(app_key)
    if sub is None:
        return JSONResponse({"error": f"unknown app '{app_key}'"}, status_code=404)

    target = f"{sub.url}/{path}"
    body = await request.body()
    async with httpx.AsyncClient() as client:
        try:
            upstream = await client.request(
                request.method,
                target,
                params=request.query_params,
                content=body,
                headers={
                    k: v
                    for k, v in request.headers.items()
                    if k.lower() not in ("host", "content-length")
                },
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"error": f"{sub.name} unavailable", "detail": str(exc)},
                status_code=502,
            )
    # A redirect is nothing but its Location header, and only content, status
    # and content-type were being copied — so an upstream 307 arrived at the
    # browser as a 307 pointing nowhere and simply did nothing. That is the
    # whole of /api/gmail/auth/login, which is how Google Calendar gets
    # reconnected, so the one repair the dashboard can offer was unreachable
    # through the front door it is served from.
    headers = {}
    location = upstream.headers.get("location")
    if location:
        headers["location"] = location
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers=headers,
    )


@app.middleware("http")
async def no_stale_static(request: Request, call_next):
    """Static assets must revalidate on every load. Without this, browsers
    heuristically cache app.js/home.js across deploys and users end up running
    week-old JS against a new page — buttons silently do nothing. no-cache
    still allows 304s, so it stays fast."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def reject_unknown_hosts(request: Request, call_next):
    """Refuse anything addressed to a name this box does not answer to.

    Every http middleware runs ahead of every route handler, so this lands
    before the proxy whatever order it is registered in — a refused request is
    never forwarded upstream, and a test pins that. Registration order only
    decides this against the OTHER middleware (cache headers), which has no
    security bearing either way; an earlier version of this comment claimed the
    ordering mattered and that a test held it, and neither was true.
    """
    if not host_is_allowed(request.headers.get("host", "")):
        # 400, matching Starlette's TrustedHostMiddleware. No credential makes
        # this request valid, so 401 and 403 would both be lies.
        return JSONResponse({"error": "invalid host header"}, status_code=400)
    return await call_next(request)


@app.middleware("http")
async def require_login(request: Request, call_next):
    """Single-user session on everything that is data (SCRUM-98).

    The rules — what is public, why unset means open-but-loud, why not by
    client IP — live in gateway/app/auth.py with their reasons. This is only
    the hook. Delegated so tests can monkeypatch `auth.PASSWORD` and drive the
    real decision, not a copy of it.
    """
    return await auth.guard(request, call_next)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
