"""Jellyfin sub-app — a read-only dashboard for your Jellyfin media server.

It's a stateless client (no database). Point it at your Jellyfin with an API key
and it surfaces the useful stuff: continue-watching, next-up, recently-added,
library counts, and what's streaming right now. It never proxies media and never
stores anything; the API key stays server-side and is never sent to the browser.

Config:
  JELLYFIN_URL      e.g. http://<homelab-ip>:8096
  JELLYFIN_API_KEY  Jellyfin dashboard -> Advanced -> API Keys
  JELLYFIN_USER_ID  optional; if unset, the first user is used
"""
import os

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Jellyfin Service")

JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")
JELLYFIN_USER_ID = os.environ.get("JELLYFIN_USER_ID", "")

_USER_ID_CACHE = JELLYFIN_USER_ID or None

MOCK = {
    "server": "Homelab Jellyfin (sample)",
    "counts": {"movies": 428, "series": 63, "episodes": 1512},
    "continue": [
        {"name": "The Bear — S2E4 “Honeydew”", "series": "The Bear", "percent": 68},
        {"name": "Dune: Part Two", "series": None, "percent": 41},
    ],
    "nextup": [
        {"name": "The Bear — S2E5 “Pop”", "series": "The Bear"},
        {"name": "Severance — S1E3 “In Perpetuity”", "series": "Severance"},
    ],
    "recent": [
        {"name": "Oppenheimer", "type": "Movie"},
        {"name": "Shōgun — S1E1", "type": "Episode"},
        {"name": "The Batman", "type": "Movie"},
    ],
    "nowplaying": [
        {"user": "ant", "item": "Dune: Part Two", "device": "Living Room TV"},
    ],
}


def _connected() -> bool:
    return bool(JELLYFIN_URL and JELLYFIN_API_KEY)


def _headers() -> dict:
    return {"X-Emby-Token": JELLYFIN_API_KEY}


async def _get(client: httpx.AsyncClient, path: str, **params) -> dict | list:
    r = await client.get(f"{JELLYFIN_URL}{path}", params=params, headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json()


async def _user_id(client: httpx.AsyncClient) -> str:
    global _USER_ID_CACHE
    if _USER_ID_CACHE:
        return _USER_ID_CACHE
    users = await _get(client, "/Users")
    _USER_ID_CACHE = users[0]["Id"] if users else ""
    return _USER_ID_CACHE


def _pct(item: dict) -> int:
    ud = item.get("UserData") or {}
    return round(ud.get("PlayedPercentage") or 0)


def _label(item: dict) -> str:
    name = item.get("Name", "")
    series = item.get("SeriesName")
    if series and item.get("Type") == "Episode":
        s, e = item.get("ParentIndexNumber"), item.get("IndexNumber")
        tag = f" — S{s}E{e}" if s and e else ""
        return f"{series}{tag} “{name}”"
    return name


async def _live() -> dict:
    async with httpx.AsyncClient() as client:
        uid = await _user_id(client)
        counts = await _get(client, "/Items/Counts")
        resume = await _get(client, f"/Users/{uid}/Items/Resume",
                            Limit=12, Recursive=True, MediaTypes="Video")
        nextup = await _get(client, "/Shows/NextUp", UserId=uid, Limit=12)
        latest = await _get(client, f"/Users/{uid}/Items/Latest", Limit=12)
        sessions = await _get(client, "/Sessions")
    return {
        "server": os.environ.get("JELLYFIN_SERVER_NAME", "Jellyfin"),
        "counts": {
            "movies": counts.get("MovieCount", 0),
            "series": counts.get("SeriesCount", 0),
            "episodes": counts.get("EpisodeCount", 0),
        },
        "continue": [{"name": _label(i), "series": i.get("SeriesName"), "percent": _pct(i)}
                     for i in (resume.get("Items", []) if isinstance(resume, dict) else resume)],
        "nextup": [{"name": _label(i), "series": i.get("SeriesName")}
                   for i in (nextup.get("Items", []) if isinstance(nextup, dict) else nextup)],
        "recent": [{"name": _label(i), "type": i.get("Type", "")} for i in latest],
        "nowplaying": [
            {"user": s.get("UserName", "?"),
             "item": _label(s.get("NowPlayingItem", {})),
             "device": s.get("DeviceName", "")}
            for s in sessions if s.get("NowPlayingItem")
        ],
    }


async def _data() -> dict:
    if not _connected():
        return MOCK
    return await _live()


@app.get("/health")
async def health():
    return {"service": "jellyfin", "mode": "live" if _connected() else "mock",
            "connected": _connected()}


@app.get("/summary")
async def summary():
    try:
        d = await _data()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "jellyfin unreachable", "detail": str(exc)}, status_code=502)
    c = d["counts"]
    return {
        "server": d["server"],
        "movies": c["movies"], "series": c["series"], "episodes": c["episodes"],
        "now_playing": len(d["nowplaying"]),
        "continue_count": len(d["continue"]),
        "mode": "live" if _connected() else "mock",
        "connected": _connected(),
    }


@app.get("/dashboard")
async def dashboard():
    """Everything the hub panel needs in one call."""
    try:
        return await _data()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "jellyfin unreachable", "detail": str(exc)}, status_code=502)


@app.get("/")
async def root():
    return {"app": "Jellyfin", "endpoints": ["/summary", "/dashboard", "/health"]}
