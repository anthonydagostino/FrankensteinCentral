import os
import urllib.parse

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse

app = FastAPI(title="Gmail Checker Service")

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8083/auth/callback")
SCOPES = "https://www.googleapis.com/auth/gmail.readonly"

# In-memory token store for a single user. Swap for encrypted DB storage later.
TOKENS: dict[str, str] = {}

# Sample inbox until OAuth is connected. One item is a scheduled interview so
# the assistant's cross-app routing has something to act on.
MOCK_NEEDS_REPLY = [
    {
        "id": "m1",
        "from": "recruiter@acme.io",
        "subject": "Interview scheduled: Fri Jul 25, 2:00 PM",
        "snippet": "We'd love to move forward. Your interview is set for Friday July 25 at 2:00 PM.",
        "category": "interview",
        "needs_reply": True,
    },
    {
        "id": "m2",
        "from": "landlord@rentals.com",
        "subject": "Lease renewal — respond by Jul 30",
        "snippet": "Please confirm whether you'd like to renew by July 30.",
        "category": "deadline",
        "needs_reply": True,
    },
    {
        "id": "m3",
        "from": "newsletter@stuff.com",
        "subject": "This week in tech",
        "snippet": "Top stories...",
        "category": "fyi",
        "needs_reply": False,
    },
]


@app.get("/health")
async def health():
    return {"service": "gmail", "connected": bool(TOKENS.get("access_token"))}


@app.get("/auth/login")
async def login():
    """Kick off Google OAuth. Requires GOOGLE_CLIENT_ID/SECRET to be set."""
    if not CLIENT_ID:
        return JSONResponse(
            {"error": "Set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET to connect Gmail."},
            status_code=400,
        )
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@app.get("/auth/callback")
async def callback(code: str | None = None, error: str | None = None):
    if error:
        return JSONResponse({"error": error}, status_code=400)
    if not code:
        return JSONResponse({"error": "missing code"}, status_code=400)
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
    if r.status_code != 200:
        return JSONResponse({"error": "token exchange failed", "detail": r.text}, status_code=502)
    tok = r.json()
    TOKENS["access_token"] = tok.get("access_token", "")
    if tok.get("refresh_token"):
        TOKENS["refresh_token"] = tok["refresh_token"]
    return RedirectResponse("http://localhost:8080/")


async def _fetch_real_inbox() -> list[dict]:
    """Fetch unread threads via the Gmail API. Returns [] on any failure."""
    token = TOKENS.get("access_token")
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"q": "is:unread -category:promotions", "maxResults": 15},
            headers=headers,
            timeout=15,
        )
        if r.status_code != 200:
            return []
        ids = [m["id"] for m in r.json().get("messages", [])]
        out = []
        for mid in ids:
            mr = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                params={"format": "metadata", "metadataHeaders": ["From", "Subject"]},
                headers=headers,
                timeout=15,
            )
            if mr.status_code != 200:
                continue
            payload = mr.json()
            hdrs = {h["name"]: h["value"] for h in payload.get("payload", {}).get("headers", [])}
            out.append(
                {
                    "id": mid,
                    "from": hdrs.get("From", ""),
                    "subject": hdrs.get("Subject", ""),
                    "snippet": payload.get("snippet", ""),
                    "category": "inbox",
                    "needs_reply": True,
                }
            )
        return out


@app.get("/needs-reply")
async def needs_reply():
    """Emails that actually want a response. Real inbox when connected, else sample."""
    if TOKENS.get("access_token"):
        real = await _fetch_real_inbox()
        if real:
            return {"emails": real, "mode": "live"}
    pending = [m for m in MOCK_NEEDS_REPLY if m["needs_reply"]]
    return {"emails": pending, "mode": "mock"}


@app.get("/")
async def root():
    return {
        "app": "Gmail Checker",
        "endpoints": ["/auth/login", "/needs-reply", "/health"],
    }
