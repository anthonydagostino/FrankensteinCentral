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

# Local-parts machines send from — never a human awaiting your reply. This is
# one of several automation signals; headers and Gmail's own category labels
# are checked too (see _is_automated).
_AUTOMATED_SENDER = re.compile(
    r"no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|notifications?@|alerts?@|"
    r"mailer|daemon|bounce|newsletters?@|news@|updates?@|info@|support@|"
    r"service@|billing@|receipts?@|statements?@|confirmations?@|accounts?@|"
    r"security@|verify@|hello@|team@|marketing@|offers?@|promotions?@|"
    r"automated?@|system@|robot|feedback@|surveys?@|invoices?@|orders?@|"
    r"shipping@|tracking@|reminders?@|customerservice|memberservices",
    re.I,
)

# Transactional life-admin notifications: orders, shipping, codes, sign-ins…
# Matched against the SUBJECT only (high precision).
_TRANSACTIONAL = re.compile(
    r"\breceipt\b|order (confirm|receiv|updat|shipp|deliver)|has (shipped|been delivered)|"
    r"out for delivery|delivery (update|confirmation|notification)|tracking (number|update)|"
    r"verification code|security (code|alert)|sign[- ]?in|new login|password (reset|change)|"
    r"one[- ]?time (code|passcode)|\b2fa\b|\botp\b|confirm your|"
    r"your (order|package|receipt|reservation|appointment|subscription|account|ticket)|"
    r"invoice\s*#?\d|booking confirm|thank you for (your payment|shopping|your order)",
    re.I,
)

# Financial/account notifications: deposits, transfers, statements, bills…
# Subject-first (high precision); body text counts only for automated senders.
_FINANCIAL = re.compile(
    r"\beft\b|\bach\b|direct deposit|deposit(ed)?\s+(received|posted|complete)|"
    r"funds?\s+(received|transferred|available)|withdrawal|transaction (alert|posted|complete)|"
    r"payment (received|posted|due|scheduled|confirmation|processed)|"
    r"statement (is )?(ready|available)|balance (alert|update|low)|card (charge|purchase)|"
    r"transfer (initiated|complete|received)|\breceived\b[^.]{0,20}\$|"
    r"your (bill|statement)|bill is ready|auto-?pay|credit card payment|"
    r"dividend|trade confirmation|interest (payment|earned)|wire transfer",
    re.I,
)

# Language that actually implies a human wants a response from YOU.
_HUMAN_ASK = re.compile(
    r"\b(can|could|would|will|did) you\b|let me know|what do you think|"
    r"are you (free|available|around|able|interested)|when (are|can|would|works)|"
    r"work(s)? for you|please (confirm|advise|respond|reply|review|send|share)|"
    r"\brsvp\b|get back to (me|us)|would love to hear|any updates?|"
    r"checking in|following up|circling back|do you (have|want|need|know)|"
    r"what time|which (day|time)|works best|your (availability|thoughts|feedback)",
    re.I,
)

# "?" occurrences that are boilerplate, not a question aimed at the recipient.
_BOILERPLATE_Q = re.compile(
    r"questions\?|questions or concerns|have questions|need help\?|"
    r"forgot (your )?password\?|\?utm_|\?id=|\?ref=|\?src=",
    re.I,
)
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



_BULK_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}


def _is_automated(msg: dict) -> bool:
    """Deterministic machine-mail detection: sender local-part, standard
    automation headers, and Gmail's own tab classification."""
    if _AUTOMATED_SENDER.search(msg.get("from", "")):
        return True
    if msg.get("list_unsubscribe"):
        return True
    auto = (msg.get("auto_submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if (msg.get("precedence") or "").strip().lower() in ("bulk", "list", "junk", "auto_reply"):
        return True
    if _BULK_LABELS.intersection(msg.get("labels") or []):
        return True
    return False


def triage(msg: dict) -> dict:
    """Classify one message.

    Categories: interview (job/application correspondence), finance
    (financial/account notifications), notification (transactional life-admin),
    deal (a concrete promo), deadline (time-sensitive), personal (likely human
    correspondence), fyi (everything automated/low-priority).

    needs_reply favors PRECISION over recall: it fires only for non-automated
    mail whose language actually implies a human expects a response — never
    for transactional/financial notifications, no matter what boilerplate
    question marks they contain.
    """
    sender = msg.get("from", "")
    subject = msg.get("subject", "")
    snippet = msg.get("snippet", "")
    text = f"{subject} {snippet}"

    if _JOB_BOARD.search(sender):
        # Bulk "you might like this job" mail — never let it look like a real
        # interview or deadline, no matter what language it uses.
        return {**msg, "category": "fyi", "needs_reply": False, "priority": 0,
                "automated": True}

    automated = _is_automated(msg)

    extra: dict = {}
    if _INTERVIEW.search(text):
        category = "interview"
    elif _FINANCIAL.search(subject) or (automated and _FINANCIAL.search(text)):
        category = "finance"
    elif _TRANSACTIONAL.search(subject):
        category = "notification"
    elif _DEAL.search(text):
        category = "deal"
        merchant, offer = _extract_deal(subject, snippet, sender)
        extra = {"merchant": merchant, "offer": offer}
    elif _DEADLINE.search(text):
        category = "deadline"
    elif automated:
        category = "fyi"
    else:
        category = "personal"

    # A question aimed at the recipient — boilerplate "Questions? Call us"
    # and URL query-strings are stripped before looking for "?".
    cleaned = _BOILERPLATE_Q.sub(" ", f"{subject} {snippet[:200]}")
    direct_question = subject.strip().endswith("?") or (
        "?" in cleaned and category in ("personal", "interview", "deadline"))
    asks = bool(_HUMAN_ASK.search(_BOILERPLATE_Q.sub(" ", text))) or direct_question

    needs_reply = (
        not automated
        and category in ("interview", "deadline", "personal")
        and (asks or category == "interview")
    )

    priority = {"interview": 3, "deadline": 3, "personal": 2, "finance": 1,
                "notification": 1, "deal": 1, "fyi": 0}[category]
    if needs_reply and priority < 1:
        priority = 1

    return {**msg, **extra, "category": category, "needs_reply": needs_reply,
            "priority": priority, "automated": automated}


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
                params={"format": "metadata",
                        "metadataHeaders": ["From", "Subject", "List-Unsubscribe",
                                            "Auto-Submitted", "Precedence", "Reply-To"]},
                headers=headers,
                timeout=15,
            )
            if mr.status_code != 200:
                continue
            payload = mr.json()
            hdrs = {h["name"].lower(): h["value"] for h in payload.get("payload", {}).get("headers", [])}
            received = payload.get("internalDate")  # epoch ms, gmail-provided
            age_hours = None
            if received:
                try:
                    age_hours = round((time.time() * 1000 - int(received)) / 3_600_000, 1)
                except (TypeError, ValueError):
                    age_hours = None
            out.append(
                {
                    "id": mid,
                    "thread_id": payload.get("threadId", mid),
                    "from": hdrs.get("from", ""),
                    "subject": hdrs.get("subject", ""),
                    "snippet": payload.get("snippet", ""),
                    "received": received,
                    "age_hours": age_hours,
                    # deterministic automation metadata
                    "list_unsubscribe": bool(hdrs.get("list-unsubscribe")),
                    "auto_submitted": hdrs.get("auto-submitted", ""),
                    "precedence": hdrs.get("precedence", ""),
                    "reply_to": hdrs.get("reply-to", ""),
                    "labels": payload.get("labelIds", []),
                }
            )
        return out


async def _current_inbox() -> tuple[list[dict], str]:
    if _connected():
        real = await _fetch_inbox()
        if real is not None:
            return triage_all(real), "live"
        # Connected but the fetch failed (expired/invalid token). Surface an
        # honest error state — never fabricate emails.
        return [], "error"
    return [], "disconnected"


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
    replied yet ("pending"). None on failure (surfaced as an error state)."""
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
                # Strip quoted/forwarded history so a thread's Nth message
                # doesn't re-surface every prior message's dates as if new.
                body = dateparse.strip_quoted(_decode_body(m.get("payload", {})))
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


@app.get("/thread-availability")
async def thread_availability():
    """Threads where you proposed times, with their current state — pending
    (no reply yet), confirmed (a slot was accepted), countered (they offered
    a different time), or declined. Bones turns these into calendar events."""
    if _connected():
        real = await _fetch_sent_proposal_threads()
        if real is not None:
            return {"threads": real, "mode": "live"}
        return {"threads": [], "mode": "error"}
    return {"threads": [], "mode": "disconnected"}


@app.get("/needs-reply")
async def needs_reply():
    """Triaged emails that actually want a response, most important first."""
    items, mode = await _current_inbox()
    return {"emails": [m for m in items if m["needs_reply"]], "mode": mode}


@app.get("/sample")
async def sample():
    """Sanitized classification sample of the WHOLE recent inbox — for judging
    classifier quality, not just the needs-reply survivors. Metadata only:
    sender domain, category, flags, age, and a server-side truncated subject.
    Never bodies or snippets."""
    items, mode = await _current_inbox()

    def _domain(addr: str) -> str:
        m = re.search(r"@([\w.-]+)", addr or "")
        return m.group(1).lower() if m else "(unknown)"

    out = []
    counts: dict[str, int] = {}
    for m in items:
        counts[m["category"]] = counts.get(m["category"], 0) + 1
        subj = m.get("subject") or "(no subject)"
        out.append({
            "domain": _domain(m.get("from", "")),
            "category": m["category"],
            "needs_reply": m["needs_reply"],
            "automated": m.get("automated", False),
            "age_hours": m.get("age_hours"),
            "subject": subj[:40] + ("…" if len(subj) > 40 else ""),
        })
    return {"mode": mode, "total": len(out), "counts": counts,
            "needs_reply_count": sum(1 for m in items if m["needs_reply"]),
            "items": out}


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
