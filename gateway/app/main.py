import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .registry import load_registry

app = FastAPI(title="FrankensteinCentral Gateway")
REGISTRY = {s.key: s for s in load_registry()}
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


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


# Path segments a browser must never reach through the gateway.
#
# `/internal/...` is the convention sub-apps use for routes meant only for
# other containers on the compose network. gmail's /internal/token hands out a
# live Google OAuth credential carrying gmail.modify AND calendar.events — read
# and modify the whole inbox, read and write the calendar.
#
# That convention was a docstring and nothing else. The proxy below forwards
# any path to any registered service and the gateway has no authentication, so
# `GET http://<box>:8080/api/gmail/internal/token` returned the credential to
# any unauthenticated caller on the network — through the same origin the
# dashboard is served on. The comment described an intent; this set enforces it.
PRIVATE_SEGMENTS = {"internal"}


def _reaches_private_route(path: str) -> bool:
    """True when `path` lands on a container-only route.

    Resolves "." and ".." BEFORE looking at the segments, because httpx
    normalises them when it builds the upstream URL: a literal check would
    wave `x/../internal/token` through and it would still arrive at
    /internal/token upstream.
    """
    resolved: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if resolved:
                resolved.pop()
            continue
        resolved.append(segment)
    return any(s.casefold() in PRIVATE_SEGMENTS for s in resolved)


@app.api_route(
    "/api/{app_key}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy(app_key: str, path: str, request: Request):
    """Reverse-proxy /api/<app>/<path> to the matching sub-app service."""
    sub = REGISTRY.get(app_key)
    if sub is None:
        return JSONResponse({"error": f"unknown app '{app_key}'"}, status_code=404)

    # 404 and not 403, worded exactly as FastAPI words a route that isn't
    # there: a refusal that says "you found something" is itself an answer.
    # Refused here, before any upstream call, so nothing reaches the sub-app.
    if _reaches_private_route(path):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

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


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
