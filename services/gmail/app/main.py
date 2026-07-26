import base64
import json
import os
import re
import time
import urllib.parse

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import dateparse

app = FastAPI(title="Gmail Checker Service")

# Where the refresh token is saved so the one-time "Allow" survives restarts.
TOKEN_FILE = os.environ.get("GMAIL_TOKEN_FILE", "/data/token.json")

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8083/auth/callback")
# gmail.modify matches the scope your PowerBuy app already holds, so its
# existing refresh token can be reused here (and lets us label/archive later).
# calendar.events was added so Bones can write pending/confirmed events to the
# real Google Calendar (schedule service borrows this token — see
# /internal/token below). Existing users need to re-visit /auth/login once
# after this scope was added; a token minted with only gmail.modify can't
# call the Calendar API and Google will 403 until re-consented.
SCOPES = "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/calendar.events"

# How much of the inbox to look at. category:primary drops promotions/social/
# updates automatically, so this is the real inbox — not just receipts.
INBOX_QUERY = os.environ.get("GMAIL_QUERY") or "category:primary newer_than:7d -from:me"
# Your own outgoing mail — this is where "I'm available Monday at 2pm" lives,
# so it never shows up in INBOX_QUERY (which explicitly excludes -from:me).
SENT_QUERY = os.environ.get("GMAIL_SENT_QUERY") or "in:sent newer_than:21d"

# Token store. Priority: env override -> saved file -> filled by the OAuth flow.
TOKENS: dict[str, str] = {}
if os.environ.get("GOOGLE_REFRESH_TOKEN"):
    TOKENS["refresh_token"] = os.environ["GOOGLE_REFRESH_TOKEN"]
_ACCESS: dict[str, float] = {}  # {"token": ..., "exp": epoch_seconds}


def _load_saved_token() -> None:
    """Load a previously saved refresh token so 'Allow' is truly one-time."""
    if TOKENS.get("refresh_token"):
        return
    try:
        with open(TOKEN_FILE) as f:
            saved = json.load(f).get("refresh_token")
            if saved:
                TOKENS["refresh_token"] = saved
    except (OSError, ValueError):
        pass


def _save_token(refresh_token: str) -> None:
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE) or ".", exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            json.dump({"refresh_token": refresh_token}, f)
    except OSError:
        pass  # non-fatal: falls back to in-memory for this run


_load_saved_token()

# Senders/subjects that are almost never worth a human reply.
_NOREPLY = re.compile(r"no[-_]?reply|do[-_]?not[-_]?reply|notifications?@|mailer@", re.I)
# Job boards/recruiting-alert services — these send bulk "you might like this
# job" mail that routinely contains real-looking deadline/interview language
# ("Applications are due...") despite being unsolicited marketing, not
# something the user applied to or committed to. Always treat as noise.
_JOB_BOARD = re.compile(
    r"joinhandshake\.com|linkedin\.com|indeed\.com|ziprecruiter\.com|"
    r"glassdoor\.com|dice\.com|monster\.com|simplyhired\.com|lensa\.com|jobot\.com",
    re.I,
)
_INTERVIEW = re.compile(r"\binterview\b|phone screen|onsite interview", re.I)
_DEADLINE = re.compile(
    r"respond by|reply by|due |deadline|expires?|by (mon|tue|wed|thu|fri|sat|sun|jan|feb|"
    r"mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
    re.I,
)
# An actual discount/promo, not just marketing noise — a real deal has a
# concrete offer (a percentage, a dollar amount, a code, or "free X").
_DEAL = re.compile(
    r"\d{1,3}\s?%\s*off|\$\d+(\.\d+)?\s*off|\bfree shipping\b|\bbogo\b|"
    r"buy one get one|promo\s*code|coupon\s*code|\buse code\b|flash sale|"
    r"\bclearance\b|\bdiscount\b",
    re.I,
)


def _extract_deal(subject: str, snippet: str, sender: str) -> tuple[str, str | None]:
    """Best-effort merchant name + the matched offer text, from sender/subject."""
    text = f"{subject} {snippet}"
    m = _DEAL.search(text)
    offer = m.group(0).strip() if m else None
    name_match = re.match(r'^"?([^"<]+?)"?\s*<', sender)
    if name_match:
        merchant = name_match.group(1).strip()
    else:
        domain_match = re.search(r"@([\w.-]+)", sender)
        merchant = domain_match.group(1) if domain_match else sender
    return merchant, offer

MOCK_INBOX = [
    {
        "id": "m1",
        "from": "recruiter@acme.io",
        "subject": "Interview scheduled: Fri Jul 25, 2:00 PM",
        "snippet": "We'd love to move forward. Your interview is set for Friday July 25 at 2:00 PM. Can you confirm?",
    },
    {
        "id": "m2",
        "from": "landlord@rentals.com",
        "subject": "Lease renewal — respond by Jul 30",
        "snippet": "Please confirm whether you'd like to renew by July 30.",
    },
    {
        "id": "m3",
        "from": "mom@family.com",
        "subject": "dinner sunday?",
        "snippet": "Are you free to come over this Sunday evening?",
    },
    {
        "id": "m4",
        "from": "no-reply@newsletter.com",
        "subject": "This week in tech",
        "snippet": "Top stories you might have missed...",
    },
    {
        "id": "m5",
        "from": "deals@grubhub.com",
        "subject": "20% off your next order",
        "snippet": "Use code SAVE20 at checkout. Free delivery on orders over $15.",
    },
]


def triage(msg: dict) -> dict:
    """Classify one message: category, whether it wants a reply, and a priority."""
    sender = msg.get("from", "")
    subject = msg.get("subject", "")
    snippet = msg.get("snippet", "")
    text = f"{subject} {snippet}"

    if _JOB_BOARD.search(sender):
        # Bulk "you might like this job" mail — never let it look like a real
        # interview or deadline, no matter what language it uses.
        return {**msg, "category": "fyi", "needs_reply": False, "priority": 0}

    automated = bool(_NOREPLY.search(sender))

    extra: dict = {}
    if _INTERVIEW.search(text):
        category = "interview"
    elif _DEADLINE.search(text):
        category = "deadline"
    elif _DEAL.search(text):
        category = "deal"
        merchant, offer = _extract_deal(subject, snippet, sender)
        extra = {"merchant": merchant, "offer": offer}
    elif automated:
        category = "fyi"
    else:
        category = "personal"

    asks_something = "?" in text or _DEADLINE.search(text) is not None
    needs_reply = (
        not automated
        and category not in ("deal", "fyi")
        and (asks_something or category in ("interview", "deadline"))
    )

    priority = {"interview": 3, "deadline": 3, "personal": 2, "deal": 1, "fyi": 0}[category]
    if needs_reply and priority < 1:
        priority = 1

    return {**msg, **extra, "category": category, "needs_reply": needs_reply, "priority": priority}


def triage_all(messages: list[dict]) -> list[dict]:
    return sorted((triage(m) for m in messages), key=lambda m: m["priority"], reverse=True)


async def _access_token() -> str | None:
    """Return a valid access token, refreshing from the refresh token as needed."""
    if _ACCESS.get("token") and _ACCESS.get("exp", 0) > time.time() + 30:
        return _ACCESS["token"]
    refresh = TOKENS.get("refresh_token")
    if not (refresh and CLIENT_ID and CLIENT_SECRET):
        return TOKENS.get("access_token")  # may exist straight from the OAuth flow
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "refresh_token": refresh,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
    if r.status_code != 200:
        return None
    tok = r.json()
    _ACCESS["token"] = tok.get("access_token", "")
    _ACCESS["exp"] = time.time() + int(tok.get("expires_in", 3600))
    return _ACCESS["token"]


def _connected() -> bool:
    return bool(TOKENS.get("refresh_token") or TOKENS.get("access_token"))


@app.get("/health")
async def health():
    return {"service": "gmail", "connected": _connected(), "query": INBOX_QUERY}


@app.get("/internal/token")
async def internal_token():
    """Access token for OTHER sub-apps on the internal docker network only —
    lets the schedule service push to Google Calendar with the same
    connected account, without a second OAuth flow. Not linked from the UI
    and not meaningful to call from outside the compose network."""
    token = await _access_token()
    if not token:
        return JSONResponse({"error": "not connected"}, status_code=503)
    return {"access_token": token}


@app.get("/auth/login")
async def login():
    """Kick off Google OAuth. Only needed if you're NOT reusing a refresh token."""
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
        _save_token(tok["refresh_token"])
    return HTMLResponse(
        "<h2>✅ Gmail connected</h2>"
        "<p>Your inbox is now live in the hub. You can close this tab.</p>"
        '<p><a href="http://localhost:8080/">← Back to the hub</a></p>'
    )


async def _fetch_inbox() -> list[dict] | None:
    """Pull recent primary-inbox messages (not just receipts). None on failure."""
    token = await _access_token()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"q": INBOX_QUERY, "maxResults": 25},
            headers=headers,
            timeout=15,
        )
        if r.status_code != 200:
            return None
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
                }
            )
        return out


async def _current_inbox() -> tuple[list[dict], str]:
    if _connected():
        real = await _fetch_inbox()
        if real is not None:
            return triage_all(real), "live"
    return triage_all(MOCK_INBOX), "mock"


_PROFILE: dict[str, str] = {}  # cached {"email": "you@gmail.com"}


async def _own_email(client: httpx.AsyncClient, headers: dict) -> str:
    if _PROFILE.get("email"):
        return _PROFILE["email"]
    r = await client.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", headers=headers, timeout=15)
    if r.status_code == 200:
        _PROFILE["email"] = r.json().get("emailAddress", "")
    return _PROFILE.get("email", "")


def _decode_body(payload: dict) -> str:
    """Best-effort plain-text extraction from a Gmail message payload,
    walking multipart MIME. Falls back to HTML-stripped text if no
    text/plain part exists (some clients only send HTML)."""
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            try:
                decoded = base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="ignore")
            except Exception:  # noqa: BLE001 - malformed/undecodable part, skip it
                decoded = ""
            if mime == "text/plain":
                plain.append(decoded)
            elif mime == "text/html":
                html.append(re.sub(r"<[^>]+>", " ", decoded))
        for p in part.get("parts", []) or []:
            walk(p)

    walk(payload)
    return "\n".join(plain) if plain else "\n".join(html)


async def _fetch_sent_proposal_threads() -> list[dict] | None:
    """Find your own 'I'm available X at Y' emails and, per thread, work out
    whether the other side has since confirmed one of the offered slots,
    countered with a different time, declined outright, or just hasn't
    replied yet ("pending"). None on failure (falls back to mock)."""
    token = await _access_token()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        own_email = await _own_email(client, headers)
        r = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"q": SENT_QUERY, "maxResults": 25},
            headers=headers,
            timeout=15,
        )
        if r.status_code != 200:
            return None
        ids = [m["id"] for m in r.json().get("messages", [])]

        # Cheap first pass (metadata only) to find which sent messages even
        # look like an availability proposal, before paying for full threads.
        candidate_threads: dict[str, None] = {}
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
            text = f"{hdrs.get('Subject', '')} {payload.get('snippet', '')}"
            if dateparse.is_availability_proposal(text):
                candidate_threads[payload["threadId"]] = None

        def is_mine(addr: str) -> bool:
            return bool(own_email) and own_email.lower() in addr.lower()

        threads_out: list[dict] = []
        for thread_id in candidate_threads:
            tr = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}",
                params={"format": "full"},
                headers=headers,
                timeout=15,
            )
            if tr.status_code != 200:
                continue
            parsed = []
            for m in tr.json().get("messages", []):
                mhdrs = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
                body = _decode_body(m.get("payload", {}))
                parsed.append(
                    {
                        "id": m["id"],
                        "from": mhdrs.get("From", ""),
                        "subject": mhdrs.get("Subject", ""),
                        "internal_date": int(m.get("internalDate", "0") or 0),
                        "text": f"{mhdrs.get('Subject', '')} {body}",
                    }
                )
            parsed.sort(key=lambda m: m["internal_date"])

            # Anchor on the LAST message from me that reads as a proposal —
            # if I re-proposed after a decline, that's the live offer.
            anchor = None
            for m in parsed:
                if is_mine(m["from"]) and dateparse.is_availability_proposal(m["text"]):
                    anchor = m
            if anchor is None:
                continue
            proposed_slots = dateparse.extract_slots(anchor["text"])
            if not proposed_slots:
                continue

            replies_after = [
                m for m in parsed if m["internal_date"] > anchor["internal_date"] and not is_mine(m["from"])
            ]
            counterparty = replies_after[-1]["from"] if replies_after else next(
                (m["from"] for m in parsed if not is_mine(m["from"])), ""
            )

            status, confirmed_slot, countered_slots = "pending", None, []
            if replies_after:
                latest = replies_after[-1]
                verdict = dateparse.classify_reply(latest["text"])
                if verdict == "confirmed":
                    status = "confirmed"
                    reply_slots = dateparse.extract_slots(latest["text"])
                    proposed_days = {p[:10] for p in proposed_slots}
                    confirmed_slot = next(
                        (s for s in reply_slots if s[:10] in proposed_days), proposed_slots[0]
                    )
                elif verdict == "declined":
                    status = "declined"
                elif verdict == "countered":
                    countered = dateparse.extract_slots(latest["text"])
                    if countered:
                        status, countered_slots = "countered", countered
                # "unclear" -> no confident signal yet, stays pending

            threads_out.append(
                {
                    "thread_id": thread_id,
                    "subject": anchor["subject"],
                    "counterparty": counterparty,
                    "proposed_slots": proposed_slots,
                    "status": status,
                    "confirmed_slot": confirmed_slot,
                    "countered_slots": countered_slots,
                    "source_message_id": anchor["id"],
                }
            )
        return threads_out


MOCK_THREADS = [
    {
        "thread_id": "t1",
        "subject": "Re: Interview availability — Acme",
        "counterparty": "recruiter@acme.io",
        "proposed_slots": ["2026-07-31T14:00:00-04:00", "2026-08-04T10:00:00-04:00"],
        "status": "pending",
        "confirmed_slot": None,
        "countered_slots": [],
        "source_message_id": "sent1",
    },
    {
        "thread_id": "t2",
        "subject": "Re: Phone screen — WidgetCo",
        "counterparty": "hr@widgetco.com",
        "proposed_slots": ["2026-07-29T15:00:00-04:00"],
        "status": "confirmed",
        "confirmed_slot": "2026-07-29T15:00:00-04:00",
        "countered_slots": [],
        "source_message_id": "sent2",
    },
]


@app.get("/thread-availability")
async def thread_availability():
    """Threads where you proposed times, with their current state — pending
    (no reply yet), confirmed (a slot was accepted), countered (they offered
    a different time), or declined. Bones turns these into calendar events."""
    if _connected():
        real = await _fetch_sent_proposal_threads()
        if real is not None:
            return {"threads": real, "mode": "live"}
    return {"threads": MOCK_THREADS, "mode": "mock"}


@app.get("/needs-reply")
async def needs_reply():
    """Triaged emails that actually want a response, most important first."""
    items, mode = await _current_inbox()
    return {"emails": [m for m in items if m["needs_reply"]], "mode": mode}


@app.get("/deals")
async def deals():
    """Real discounts/promos spotted in the inbox — never needs a reply."""
    items, mode = await _current_inbox()
    return {"deals": [m for m in items if m["category"] == "deal"], "mode": mode}


@app.get("/summary")
async def summary():
    """Counts across the whole primary inbox, by category."""
    items, mode = await _current_inbox()
    by_cat: dict[str, int] = {}
    for m in items:
        by_cat[m["category"]] = by_cat.get(m["category"], 0) + 1
    return {
        "total": len(items),
        "needs_reply": sum(1 for m in items if m["needs_reply"]),
        "by_category": by_cat,
        "mode": mode,
    }


@app.get("/")
async def root():
    return {
        "app": "Gmail Checker",
        "endpoints": ["/needs-reply", "/thread-availability", "/deals", "/summary",
                      "/auth/login", "/health"],
    }
