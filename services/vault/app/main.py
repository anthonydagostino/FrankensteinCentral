"""Vault sub-app — a READ-ONLY health dashboard for your Vaultwarden/Bitwarden.

Security stance:
  * This service NEVER stores passwords. There is no database. It reads items
    from your vault, computes health metrics, and throws the secrets away.
  * The API only ever returns metadata + issue flags (name, username, host,
    "reused"/"weak"/...). Actual passwords are never sent to the browser.
  * The vault itself stays in Vaultwarden on your homelab. This is just a client.

Providers (VAULT_MODE):
  off        — not connected; the dashboard shows an empty "connect me" state.
  bitwarden  — reads from the Bitwarden CLI's local REST API (`bw serve`), set
               BW_SERVE_URL to it (e.g. http://homelab-ip:8087). The CLI must be
               unlocked. Wire this up when you're home.
"""
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Vault Service")

VAULT_MODE = os.environ.get("VAULT_MODE", "off").strip().lower()
BW_SERVE_URL = os.environ.get("BW_SERVE_URL", "").rstrip("/")

COMMON = {
    "password", "123456", "12345678", "hunter2", "password123", "qwerty",
    "letmein", "111111", "abc123", "iloveyou", "admin", "welcome",
}


def _connected() -> bool:
    return VAULT_MODE == "bitwarden" and bool(BW_SERVE_URL)


async def _fetch_raw_items() -> list[dict]:
    """Return raw items (with passwords) for server-side analysis only."""
    if not _connected():
        return []
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{BW_SERVE_URL}/list/object/items", timeout=20)
        r.raise_for_status()
        out = []
        for it in r.json().get("data", {}).get("data", []):
            login = it.get("login") or {}
            uris = [u.get("uri", "") for u in (login.get("uris") or []) if u.get("uri")]
            out.append({
                "name": it.get("name", ""),
                "username": login.get("username", ""),
                "folder": it.get("folderId") or "",
                "uris": uris,
                "password": login.get("password") or "",
                "revised": it.get("revisionDate", ""),
                "totp": bool(login.get("totp")),
            })
        return out


def _is_weak(pw: str) -> bool:
    if not pw:
        return True
    if pw.lower() in COMMON:
        return True
    if len(pw) < 12:
        return True
    classes = sum(bool(re.search(p, pw)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    return classes < 3


def _is_old(revised: str) -> bool:
    if not revised:
        return False
    try:
        dt = datetime.fromisoformat(revised.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days > 365


def _hosts(uris: list[str]) -> list[str]:
    hosts = []
    for u in uris:
        try:
            h = urlparse(u if "://" in u else f"//{u}").hostname
            if h:
                hosts.append(h)
        except ValueError:
            pass
    return hosts


def analyze(items: list[dict]) -> tuple[list[dict], dict]:
    """Return (client-safe item metadata + issues, summary counts). No secrets."""
    # reuse map: which password values appear more than once
    counts: dict[str, int] = {}
    for it in items:
        pw = it.get("password", "")
        if pw:
            counts[pw] = counts.get(pw, 0) + 1

    safe_items = []
    totals = {"weak": 0, "reused": 0, "old": 0, "http": 0, "no_totp": 0}
    for it in items:
        pw = it.get("password", "")
        issues = []
        if _is_weak(pw):
            issues.append("weak"); totals["weak"] += 1
        if pw and counts.get(pw, 0) > 1:
            issues.append("reused"); totals["reused"] += 1
        if _is_old(it.get("revised", "")):
            issues.append("old"); totals["old"] += 1
        if any(u.lower().startswith("http://") for u in it.get("uris", [])):
            issues.append("insecure-url"); totals["http"] += 1
        if not it.get("totp"):
            issues.append("no-2fa"); totals["no_totp"] += 1
        safe_items.append({
            "name": it.get("name", ""),
            "username": it.get("username", ""),
            "folder": it.get("folder", "") or "—",
            "hosts": _hosts(it.get("uris", [])),
            "issues": issues,
        })

    n = len(items)
    penalty = totals["weak"] * 5 + totals["reused"] * 4 + totals["http"] * 3 + totals["old"] * 1
    score = max(0, 100 - penalty) if n else 100
    folders = len({it.get("folder", "") for it in items if it.get("folder")})
    summary = {
        "total": n, "folders": folders, "score": score,
        **totals,
        "mode": "live" if _connected() else "disconnected",
        "connected": _connected(),
    }
    # worst items first (most issues)
    safe_items.sort(key=lambda x: len(x["issues"]), reverse=True)
    return safe_items, summary


@app.get("/health")
async def health():
    return {"service": "vault", "mode": "live" if _connected() else "disconnected",
            "connected": _connected()}


@app.get("/summary")
async def summary():
    try:
        items = await _fetch_raw_items()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "vault unreachable", "detail": str(exc)}, status_code=502)
    _, s = analyze(items)
    return s


@app.get("/items")
async def items():
    """Metadata + issue flags only — never passwords."""
    try:
        raw = await _fetch_raw_items()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": "vault unreachable", "detail": str(exc)}, status_code=502)
    safe, _ = analyze(raw)
    return {"items": safe, "count": len(safe)}


@app.get("/")
async def root():
    return {"app": "Vault", "endpoints": ["/summary", "/items", "/health"],
            "note": "read-only vault health; secrets never leave Vaultwarden"}
