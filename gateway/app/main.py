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
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
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
