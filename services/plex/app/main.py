"""Plex sub-app — a read-only dashboard for a Plex server shared with you.

Stateless client (no database). Signs requests with YOUR Plex account token
(X-Plex-Token) and auto-discovers the shared server through plex.tv, so it
works even though the server itself lives on someone else's network. Surfaces
continue-watching / on-deck, recently added, and libraries, with deep links
into app.plex.tv. Shared (non-owner) accounts cannot see who's streaming, so
there is no sessions panel by design.

The token stays server-side and is never sent to the browser.

Config:
  PLEX_TOKEN        your Plex account token (docs/SETUP-PLEX.md shows how to get it)
  PLEX_SERVER_NAME  optional; pick this server if several are shared with you
  PLEX_URL          optional; skip discovery and use this base URL directly
"""
import os
import time

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Plex Service")

PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")
PLEX_SERVER_NAME = os.environ.get("PLEX_SERVER_NAME", "")
PLEX_URL = os.environ.get("PLEX_URL", "").rstrip("/")
PLEX_TV = os.environ.get("PLEX_TV_BASE", "https://plex.tv").rstrip("/")

HEADERS = {
    "Accept": "application/json",
    "X-Plex-Client-Identifier": "frankensteincentral-hub",
    "X-Plex-Product": "FrankensteinCentral",
}

# discovered server cache: {"at": ts, "base": url, "machine": id, "name": str}
_SERVER: dict = {}
_SERVER_TTL = 600

EMPTY = {
    "server": "Plex",
    "libraries": [],
    "continue": [],
    "recent": [],
}


def _connected() -> bool:
    return bool(PLEX_TOKEN)


def _auth() -> dict:
    return {**HEADERS, "X-Plex-Token": PLEX_TOKEN}


async def _discover(client: httpx.AsyncClient) -> tuple[str, str, str] | None:
    """(base_url, machine_id, name) for the shared server, via plex.tv.
    Tries each advertised connection (direct first, relay last) until one
    answers. Cached for a few minutes."""
    now = time.time()
    if _SERVER.get("base") and now - _SERVER.get("at", 0) < _SERVER_TTL:
        return _SERVER["base"], _SERVER["machine"], _SERVER["name"]

    if PLEX_URL:
        # explicit override: trust it, learn the machine id from /identity
        try:
            r = await client.get(f"{PLEX_URL}/identity", headers=_auth(), timeout=8)
            r.raise_for_status()
            machine = r.json().get("MediaContainer", {}).get("machineIdentifier", "")
            _SERVER.update({"at": now, "base": PLEX_URL, "machine": machine,
                            "name": PLEX_SERVER_NAME or "Plex"})
            return PLEX_URL, machine, _SERVER["name"]
        except Exception:  # noqa: BLE001
            return None

    try:
        r = await client.get(f"{PLEX_TV}/api/v2/resources",
                             params={"includeHttps": 1, "includeRelay": 1},
                             headers=_auth(), timeout=10)
        r.raise_for_status()
        resources = r.json()
    except Exception:  # noqa: BLE001
        return None

    servers = [x for x in resources
               if "server" in (x.get("provides") or "")]
    if PLEX_SERVER_NAME:
        servers = [s for s in servers
                   if s.get("name", "").lower() == PLEX_SERVER_NAME.lower()] or servers
    for s in servers:
        conns = sorted(s.get("connections") or [],
                       key=lambda c: (bool(c.get("relay")), bool(c.get("local"))))
        for c in conns:
            uri = (c.get("uri") or "").rstrip("/")
            if not uri:
                continue
            try:
                probe = await client.get(f"{uri}/identity", headers=_auth(), timeout=6)
                probe.raise_for_status()
                machine = (probe.json().get("MediaContainer", {})
                           .get("machineIdentifier", s.get("clientIdentifier", "")))
                _SERVER.update({"at": now, "base": uri, "machine": machine,
                                "name": s.get("name", "Plex")})
                return uri, machine, s.get("name", "Plex")
            except Exception:  # noqa: BLE001
                continue
    return None


def _label(item: dict) -> str:
    if item.get("type") == "episode":
        show = item.get("grandparentTitle", "")
        s, e = item.get("parentIndex"), item.get("index")
        tag = f" — S{s}E{e}" if s is not None and e is not None else ""
        return f"{show}{tag} “{item.get('title','')}”"
    return item.get("title", "")


def _pct(item: dict) -> int:
    off, dur = item.get("viewOffset"), item.get("duration")
    if not off or not dur:
        return 0
    return min(100, round(off * 100 / dur))


async def _live() -> dict:
    async with httpx.AsyncClient() as client:
        found = await _discover(client)
        if not found:
            raise RuntimeError("no reachable Plex server for this token")
        base, machine, name = found

        libs = await client.get(f"{base}/library/sections", headers=_auth(), timeout=10)
        libs.raise_for_status()
        sections = libs.json().get("MediaContainer", {}).get("Directory", [])

        ondeck = await client.get(f"{base}/library/onDeck",
                                  params={"X-Plex-Container-Size": 12},
                                  headers=_auth(), timeout=10)
        deck = (ondeck.json().get("MediaContainer", {}).get("Metadata", [])
                if ondeck.status_code == 200 else [])

        recent = await client.get(f"{base}/library/recentlyAdded",
                                  params={"X-Plex-Container-Size": 12},
                                  headers=_auth(), timeout=10)
        added = (recent.json().get("MediaContainer", {}).get("Metadata", [])
                 if recent.status_code == 200 else [])

        libraries = []
        for sec in sections:
            entry = {"title": sec.get("title", ""), "type": sec.get("type", ""), "count": None}
            try:
                cr = await client.get(
                    f"{base}/library/sections/{sec.get('key')}/all",
                    params={"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 0},
                    headers=_auth(), timeout=8)
                if cr.status_code == 200:
                    entry["count"] = cr.json().get("MediaContainer", {}).get("totalSize")
            except Exception:  # noqa: BLE001
                pass
            libraries.append(entry)

    return {
        "server": name,
        "machine": machine,
        "libraries": libraries,
        "continue": [{"name": _label(i), "percent": _pct(i), "id": i.get("ratingKey")}
                     for i in deck],
        "recent": [{"name": _label(i), "type": i.get("type", ""), "id": i.get("ratingKey")}
                   for i in added],
    }


def _web_url(machine: str | None) -> str | None:
    if not machine:
        return "https://app.plex.tv/desktop"
    return f"https://app.plex.tv/desktop/#!/server/{machine}"


async def _data() -> dict:
    if not _connected():
        return dict(EMPTY)
    return await _live()


@app.get("/health")
async def health():
    return {"service": "plex", "mode": "live" if _connected() else "disconnected",
            "connected": _connected()}


@app.get("/summary")
async def summary():
    try:
        d = await _data()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "plex unreachable", "detail": str(exc)}, status_code=502)
    return {
        "server": d.get("server", "Plex"),
        "libraries": len(d.get("libraries", [])),
        "continue_count": len(d.get("continue", [])),
        "web_url": _web_url(d.get("machine")) if _connected() else None,
        "mode": "live" if _connected() else "disconnected",
        "connected": _connected(),
    }


@app.get("/dashboard")
async def dashboard():
    try:
        d = await _data()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "plex unreachable", "detail": str(exc)}, status_code=502)
    machine = d.pop("machine", None)
    return {**d, "web_url": _web_url(machine) if _connected() else None,
            "connected": _connected()}


@app.get("/")
async def root():
    return {"app": "Plex", "endpoints": ["/summary", "/dashboard", "/health"]}
