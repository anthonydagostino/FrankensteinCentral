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


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
