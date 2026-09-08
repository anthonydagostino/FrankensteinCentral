import asyncio
import base64
import html
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

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
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
SCOPES = f"https://www.googleapis.com/auth/gmail.modify {CALENDAR_SCOPE}"

# How much of the inbox to look at. category:primary drops promotions/social/
# updates automatically, so this is the real inbox — not just receipts.
INBOX_QUERY = os.environ.get("GMAIL_QUERY") or "category:primary newer_than:7d -from:me"
# Your own outgoing mail — this is where "I'm available Monday at 2pm" lives,
# so it never shows up in INBOX_QUERY (which explicitly excludes -from:me).
SENT_QUERY = os.environ.get("GMAIL_SENT_QUERY") or "in:sent newer_than:21d"

# Background refresh cadence. Gmail is polled on this schedule regardless of
# whether anyone opens the dashboard, and endpoints serve the last sync's
# result rather than fetching per request (one homepage load used to cost a
# list call plus one GET per message). 6 hours by default.
REFRESH_SECONDS = int(os.environ.get("GMAIL_REFRESH_SECONDS", "21600"))
# Floor between manual refreshes so repeated clicks can't hammer the API.
MANUAL_MIN_SECONDS = int(os.environ.get("GMAIL_MANUAL_MIN_SECONDS", "60"))
# Last-known-good inbox + sync bookkeeping, persisted so a container restart
# doesn't blank the Inbox card until the next scheduled poll.
STATE_FILE = os.environ.get("GMAIL_STATE_FILE", "/data/gmail_state.json")

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


# Message age and sync age are different facts, tracked separately: a message
# can be 72h old while the inbox was checked 4 minutes ago.
SYNC: dict = {
    "items": [],                   # last-known-good triaged messages
    "mode": "never",               # live | error | disconnected | never
    "last_successful_sync": None,  # ISO8601 — when mail was last actually got
    "last_attempt": None,          # ISO8601 — when a fetch was last tried
    "sync_status": "never",        # healthy | failed | never
    "last_error": None,
    "message_count": 0,
    "last_manual": 0.0,            # monotonic-ish epoch for debouncing
}


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(),
                                  tz=timezone.utc).isoformat(timespec="seconds")


def _load_state() -> None:
    """Restore last-known-good inbox so a restart doesn't blank the card."""
    try:
        with open(STATE_FILE) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return
    for k in ("items", "mode", "last_successful_sync", "last_attempt",
              "sync_status", "message_count"):
        if k in saved:
            SYNC[k] = saved[k]


def _save_state() -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump({k: SYNC[k] for k in
                       ("items", "mode", "last_successful_sync", "last_attempt",
                        "sync_status", "message_count")}, f)
    except OSError:
        pass  # non-fatal; in-memory state still serves this process


_load_state()

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
    # Google echoes the scopes the REFRESH TOKEN actually carries, which is
    # not necessarily what SCOPES asks for: a refresh token minted before
    # calendar.events was added (e.g. the one reused from PowerBuy) keeps its
    # original, narrower grant forever. Recording it here is what lets the
    # schedule service say "re-consent" instead of "unreachable" when the
    # Calendar API refuses the token — see granted_scopes below.
    _ACCESS["scope"] = tok.get("scope", "")
    return _ACCESS["token"]


def granted_scopes() -> list[str]:
    """Scopes the current credential actually holds, as last reported by
    Google. Empty until a token has been minted — absence of the list is
    "not established yet", never "no scopes"."""
    return sorted(s for s in (_ACCESS.get("scope") or "").split(" ") if s)


def has_calendar_scope() -> bool | None:
    """True/False once Google has told us, None while unestablished.

    Three states on purpose. Returning False for "we have not asked yet"
    would put a re-consent banner in front of a connection that is fine.
    """
    scopes = granted_scopes()
    if not scopes:
        return None
    return CALENDAR_SCOPE in scopes


def _connected() -> bool:
    return bool(TOKENS.get("refresh_token") or TOKENS.get("access_token"))


@app.get("/health")
async def health():
    return {"service": "gmail", "connected": _connected(), "query": INBOX_QUERY,
            "calendar_scope": has_calendar_scope(), "scopes": granted_scopes()}


@app.get("/internal/token")
async def internal_token():
    """Access token for OTHER sub-apps on the internal docker network only —
    lets the schedule service push to Google Calendar with the same
    connected account, without a second OAuth flow. Not linked from the UI
    and not meaningful to call from outside the compose network."""
    token = await _access_token()
    if not token:
        return JSONResponse({"error": "not connected"}, status_code=503)
    # `scopes` travels with the token so the caller can tell a credential that
    # was never granted Calendar access apart from a Calendar outage. Without
    # it a 403 is indistinguishable from a network failure, and the dashboard
    # tells you to wait when what it should say is "click reconnect".
    return {"access_token": token, "scopes": granted_scopes(),
            "calendar_scope": has_calendar_scope()}


# --- the browser-facing OAuth pages -----------------------------------------
#
# Google will only accept an `http://` redirect URI when its host is localhost
# or 127.0.0.1 — a LAN address like http://192.168.1.50:8080/... is rejected
# outright in the Cloud console, so the callback CANNOT be pointed at the box's
# network address. That is fine when you run the flow in a browser on the
# OptiPlex itself, and broken everywhere else: Google sends your laptop to
# `localhost:8083`, which is your laptop, where nothing is listening. The page
# fails to load and the connection is never finished.
#
# The rescue is that the failed page still has `?code=...` in the address bar.
# It was delivered; only the thing meant to receive it was on the wrong
# machine. `/auth/finish` takes that pasted address and completes the exchange
# server-side, so the flow works from any device with nothing to configure.

PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect Google — FrankensteinCentral</title>
<style>
 body{{background:#0d0f14;color:#e7ecf3;font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
      margin:0;padding:28px 20px;display:flex;justify-content:center}}
 main{{width:100%;max-width:620px}}
 h2{{margin:0 0 4px;font-size:20px}} p{{margin:10px 0}}
 .muted{{color:#8a93a6;font-size:13px}}
 .go{{display:inline-block;margin:14px 0;background:#7c9cff;color:#0d0f14;font-weight:700;
      text-decoration:none;border-radius:9px;padding:11px 20px}}
 .go:hover{{background:#6ee7b7}}
 .box{{background:#161a23;border:1px solid #262c3a;border-radius:11px;padding:14px 16px;margin:18px 0}}
 code{{background:#1d2230;border:1px solid #262c3a;border-radius:5px;padding:1px 6px;
       font-size:12.5px;overflow-wrap:anywhere}}
 input{{width:100%;font:13px ui-monospace,SFMono-Regular,Menlo,monospace;margin:8px 0;
        background:#0d0f14;color:#e7ecf3;border:1px solid #262c3a;border-radius:7px;padding:9px 10px}}
 button{{font:inherit;font-weight:700;background:#6ee7b7;color:#0d0f14;border:0;
         border-radius:8px;padding:9px 16px;cursor:pointer}}
 .warn{{border-left:3px solid #ff7a7a}} .ok{{border-left:3px solid #6ee7b7}}
</style>
<main>{body}</main>"""


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(PAGE.format(body=body))


def _consent_url() -> str:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def _rescue_form(note: str = "") -> str:
    """The paste box. Deliberately shown BEFORE anything goes wrong, because
    once the callback page fails to load there is no link left to follow —
    the browser is sitting on an error page belonging to no site at all."""
    return f"""
<form class="box" method="post" action="finish">
  <b>If the page after Google says "site can't be reached"</b>
  <p class="muted">That is expected unless this browser is running on the
  OptiPlex itself: Google is required to send the code back to
  <code>{esc(REDIRECT_URI)}</code>, and <code>localhost</code> means
  <em>this</em> device. Nothing is broken and nothing is lost — the code is
  in the failed page's address bar. Copy that whole address and paste it
  here.</p>
  <input name="pasted" autocomplete="off" spellcheck="false"
         placeholder="http://localhost:8083/auth/callback?code=4/0Ab...&amp;scope=...">
  <button type="submit">Finish connecting</button>
  {note}
</form>"""


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _extract_code(pasted: str) -> str | None:
    """The authorization code out of whatever got pasted: the whole address,
    just the query string, or the bare code."""
    pasted = (pasted or "").strip()
    if not pasted:
        return None
    query = urllib.parse.urlparse(pasted).query or pasted
    found = urllib.parse.parse_qs(query).get("code")
    if found and found[0].strip():
        return found[0].strip()
    # A bare code, pasted on its own. Google's codes look like "4/0AeanS0b…":
    # slashes, dashes and underscores, but never a scheme, a query delimiter
    # or whitespace. Anything carrying those was an address we failed to find
    # a code in, and returning it as one would send the URL itself to Google
    # and report a baffling refusal instead of "paste the whole address".
    looks_like_a_url = "://" in pasted or any(c in pasted for c in "?&= \t")
    return None if looks_like_a_url else pasted


@app.get("/auth/login")
async def login():
    """The connect page: a link to Google's consent screen, plus the rescue
    form for the redirect that cannot reach a browser off the box."""
    if not CLIENT_ID or not CLIENT_SECRET:
        return _page(
            "<h2>Google isn't configured yet</h2>"
            "<div class='box warn'><p>Set <code>GOOGLE_CLIENT_ID</code> and "
            "<code>GOOGLE_CLIENT_SECRET</code> in your <code>.env</code>, then "
            "restart the stack.</p><p class='muted'>See docs/SETUP-GMAIL.md for "
            "where to get them.</p></div>")
    return _page(
        "<h2>Connect Google</h2>"
        "<p>One approval covers both Gmail and Google Calendar. Make sure you "
        "leave the calendar permission ticked — unticking it is what leaves the "
        "week grid showing local events only.</p>"
        f"<a class='go' href='{esc(_consent_url())}'>Continue to Google →</a>"
        + _rescue_form())


async def _exchange(code: str) -> tuple[bool, str]:
    """Trade an authorization code for tokens and adopt them.

    Returns (ok, html). Shared by the callback and the paste form so both
    routes save the credential identically — a token obtained by pasting is
    exactly as good as one that arrived by redirect.
    """
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
        detail = r.text[:400]
        hint = ""
        if "invalid_grant" in detail:
            hint = ("<p class='muted'>An authorization code works once and "
                    "expires after a few minutes. Start again from "
                    "<b>Continue to Google</b> above and paste the new "
                    "address promptly.</p>")
        elif "redirect_uri_mismatch" in detail:
            hint = (f"<p class='muted'>Google Cloud console → Credentials → your "
                    f"OAuth client → Authorized redirect URIs must contain exactly "
                    f"<code>{esc(REDIRECT_URI)}</code>.</p>")
        return False, ("<div class='box warn'><b>Google refused the code.</b>"
                       f"{hint}<p class='muted'>{esc(detail)}</p></div>")

    tok = r.json()
    TOKENS["access_token"] = tok.get("access_token", "")
    if tok.get("refresh_token"):
        TOKENS["refresh_token"] = tok["refresh_token"]
        _save_token(tok["refresh_token"])
    # Adopt the freshly consented credential immediately. Without this the
    # cached access token from the OLD, narrower grant stays valid for up to
    # an hour, so re-consenting to add Calendar appears to do nothing —
    # exactly the symptom that made the connection look permanently broken.
    _ACCESS["token"] = tok.get("access_token", "")
    _ACCESS["exp"] = time.time() + int(tok.get("expires_in", 3600))
    _ACCESS["scope"] = tok.get("scope", "")

    cal = has_calendar_scope()
    cal_line = (
        "<div class='box ok'>📅 <b>Google Calendar access granted.</b> Your real "
        "calendar will appear on the week grid within about 15 minutes, or "
        "immediately if you restart the schedule service.</div>" if cal
        else "<div class='box warn'>⚠ <b>Gmail is connected, but Google did not "
             "grant calendar access.</b> Run this again and leave the calendar "
             "permission ticked, or the week grid will only ever show events "
             "stored here.</div>")
    return True, ("<h2>✅ Google connected</h2>"
                  "<p>Your inbox is live in the hub.</p>" + cal_line +
                  "<p class='muted'>You can close this tab. Refresh your "
                  "dashboard to see the change.</p>")


@app.get("/auth/callback")
async def callback(code: str | None = None, error: str | None = None):
    """Where Google sends the browser. Only reachable when the browser is on
    the same machine as this service — see the note above `PAGE`."""
    if error:
        return _page(f"<h2>Google returned an error</h2>"
                     f"<div class='box warn'><p>{esc(error)}</p></div>"
                     + _rescue_form())
    if not code:
        return _page("<h2>No authorization code in that address</h2>"
                     + _rescue_form())
    ok, body = await _exchange(code)
    return _page(body if ok else body + _rescue_form())


@app.post("/auth/finish")
async def finish(request: Request):
    """Complete the connection from the address of the page that failed.

    The form body is parsed by hand rather than with fastapi's `Form(...)`,
    which needs `python-multipart`. This service's requirements.txt does not
    list it, and a connect page that 500s on the box because of a missing
    dependency is worse than four lines of urllib.
    """
    raw = (await request.body()).decode("utf-8", "replace")
    pasted = urllib.parse.parse_qs(raw).get("pasted", [""])[0]
    code = _extract_code(pasted)
    if not code:
        return _page("<h2>Couldn't find a code in that</h2>"
                     "<div class='box warn'><p>Paste the <em>whole</em> address "
                     "of the page that failed to load — it should contain "
                     "<code>code=</code>.</p></div>" + _rescue_form())
    ok, body = await _exchange(code)
    return _page(body if ok else body + _rescue_form())


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


async def _refresh(trigger: str = "scheduled") -> dict:
    """Fetch Gmail, re-run classification, and record the outcome.

    A failed refresh NEVER destroys last-known-good mail: the previous items
    stay served and sync_status flips to "failed" so the UI can say the data
    is old rather than pretending the inbox is empty. Read-only — this never
    modifies, sends, labels or archives anything.
    """
    SYNC["last_attempt"] = _iso()
    if not _connected():
        SYNC.update(mode="disconnected", sync_status="never",
                    last_error="no Gmail credentials")
        _save_state()
        return _sync_meta()

    real = await _fetch_inbox()
    if real is None:
        # Keep items/last_successful_sync exactly as they were.
        SYNC.update(sync_status="failed", last_error="Gmail fetch failed")
        if SYNC["items"]:
            SYNC["mode"] = "live"      # serving last-known-good
        else:
            SYNC["mode"] = "error"     # nothing good was ever stored
        _save_state()
        return _sync_meta()

    SYNC.update(items=triage_all(real), mode="live", sync_status="healthy",
                last_successful_sync=_iso(), last_error=None,
                message_count=len(real))
    _save_state()
    return _sync_meta()


def _sync_meta() -> dict:
    """Sync bookkeeping for consumers. Never includes mail content."""
    last = SYNC.get("last_successful_sync")
    next_at = None
    if last:
        try:
            next_at = _iso(datetime.fromisoformat(last).timestamp() + REFRESH_SECONDS)
        except ValueError:
            next_at = None
    return {
        "last_successful_sync": last,
        "last_attempt": SYNC.get("last_attempt"),
        "sync_status": SYNC.get("sync_status"),
        "refresh_interval_seconds": REFRESH_SECONDS,
        "next_refresh_at": next_at,
        "message_count": SYNC.get("message_count", 0),
        "error": SYNC.get("last_error"),
    }


async def _refresh_loop() -> None:
    """Poll Gmail every REFRESH_SECONDS, independent of anyone opening the
    dashboard. One refresh at startup so a restart doesn't wait 6 hours."""
    await asyncio.sleep(10)  # let the container settle before the first call
    while True:
        try:
            await _refresh("scheduled")
        except Exception:  # noqa: BLE001 - the loop must never die
            SYNC.update(sync_status="failed", last_error="refresh loop error")
        await asyncio.sleep(REFRESH_SECONDS)


@app.on_event("startup")
async def _start_refresh_loop() -> None:
    asyncio.create_task(_refresh_loop())


async def _current_inbox() -> tuple[list[dict], str]:
    """Serve the last sync. Endpoints deliberately do NOT fetch: refreshes are
    scheduled (or manual), so opening the homepage costs zero Gmail calls."""
    if not _connected():
        return [], "disconnected"
    return SYNC.get("items", []), SYNC.get("mode", "never")


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


@app.get("/sync-status")
async def sync_status():
    """Sync bookkeeping only — no mail content, no credentials."""
    return {"connected": _connected(), "mode": SYNC.get("mode"), **_sync_meta()}


@app.post("/refresh")
async def refresh_now():
    """Manual 'check now'. Debounced so repeated clicks can't hammer Gmail.

    Read-only, like every other path here: it re-fetches and re-classifies,
    and never mutates mail.
    """
    now = time.time()
    since = now - (SYNC.get("last_manual") or 0)
    if since < MANUAL_MIN_SECONDS:
        return {"refreshed": False,
                "reason": f"just refreshed {int(since)}s ago",
                "retry_after_seconds": int(MANUAL_MIN_SECONDS - since),
                **_sync_meta()}
    SYNC["last_manual"] = now
    meta = await _refresh("manual")
    return {"refreshed": meta["sync_status"] == "healthy", **meta}


@app.get("/needs-reply")
async def needs_reply():
    """Triaged emails that actually want a response, most important first."""
    items, mode = await _current_inbox()
    return {"emails": [m for m in items if m["needs_reply"]], "mode": mode,
            "sync": _sync_meta()}


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
            "sync": _sync_meta(), "items": out}


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
        "sync": _sync_meta(),
    }


@app.get("/")
async def root():
    return {
        "app": "Gmail Checker",
        "endpoints": ["/needs-reply", "/thread-availability", "/deals", "/summary",
                      "/sync-status", "/refresh", "/auth/login", "/health"],
        "refresh_interval_seconds": REFRESH_SECONDS,
    }
