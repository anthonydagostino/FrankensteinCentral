"""Gmail background-sync tests.

The rules these lock down:
  * endpoints serve the last sync — opening the homepage costs 0 Gmail calls
  * a FAILED refresh never destroys last-known-good mail
  * message age and sync age stay separate facts
  * manual refresh is debounced
  * the service stays read-only
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from conftest import load_service_module  # noqa: E402

gm = load_service_module("gmail_main", "services/gmail/app/main.py")


def _msg(mid="m1", subject="Hi", frm="a@b.com", age_ms=0):
    return {"id": mid, "thread_id": mid, "from": frm, "subject": subject,
            "snippet": "", "received": str(int(time.time() * 1000) - age_ms),
            "age_hours": age_ms / 3_600_000, "list_unsubscribe": False,
            "auto_submitted": "", "precedence": "", "reply_to": "",
            "labels": ["INBOX"]}


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(gm, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(gm, "TOKENS", {"refresh_token": "x"})
    monkeypatch.setattr(gm, "CLIENT_ID", "id")
    monkeypatch.setattr(gm, "CLIENT_SECRET", "secret")
    gm.SYNC.update(items=[], mode="never", last_successful_sync=None,
                   last_attempt=None, sync_status="never", last_error=None,
                   message_count=0, last_manual=0.0)
    yield


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---- the core guarantee -------------------------------------------------

def test_successful_refresh_stores_mail_and_marks_healthy(monkeypatch):
    monkeypatch.setattr(gm, "_fetch_inbox", lambda: _async([_msg(), _msg("m2")]))
    meta = run(gm._refresh())
    assert meta["sync_status"] == "healthy"
    assert meta["last_successful_sync"]
    assert gm.SYNC["message_count"] == 2
    assert len(gm.SYNC["items"]) == 2


def test_failed_refresh_preserves_last_known_good(monkeypatch):
    """The whole point: a failure must not empty the inbox card."""
    monkeypatch.setattr(gm, "_fetch_inbox", lambda: _async([_msg("keep")]))
    run(gm._refresh())
    good_sync = gm.SYNC["last_successful_sync"]
    good_items = list(gm.SYNC["items"])

    # Pin a later clock so "attempt advanced, success did not" is unambiguous
    # rather than depending on both landing in different wall-clock seconds.
    later = "2099-01-01T00:00:00+00:00"
    monkeypatch.setattr(gm, "_iso", lambda ts=None: later)
    monkeypatch.setattr(gm, "_fetch_inbox", lambda: _async(None))   # Gmail down
    meta = run(gm._refresh())

    assert meta["sync_status"] == "failed"
    assert gm.SYNC["items"] == good_items, "last-known-good mail was destroyed"
    assert gm.SYNC["last_successful_sync"] == good_sync, "success time moved on a failure"
    assert meta["last_attempt"] == later, "attempt time should still advance"
    items, mode = run(gm._current_inbox())
    assert len(items) == 1 and mode == "live"


def test_failure_with_no_prior_data_is_an_honest_error(monkeypatch):
    monkeypatch.setattr(gm, "_fetch_inbox", lambda: _async(None))
    run(gm._refresh())
    items, mode = run(gm._current_inbox())
    assert items == [] and mode == "error"


# ---- endpoints must not fetch ------------------------------------------

def test_reading_the_inbox_makes_no_gmail_calls(monkeypatch):
    calls = []

    def counting_fetch():
        calls.append(1)
        return _async([_msg()])

    monkeypatch.setattr(gm, "_fetch_inbox", counting_fetch)
    run(gm._refresh())
    assert len(calls) == 1
    for _ in range(5):                      # five "homepage loads"
        run(gm._current_inbox())
    assert len(calls) == 1, "endpoints fetched Gmail instead of serving the sync"


# ---- sync age vs message age -------------------------------------------

def test_message_age_and_sync_age_are_separate(monkeypatch):
    old_message = _msg(age_ms=72 * 3_600_000)         # 72h old mail
    monkeypatch.setattr(gm, "_fetch_inbox", lambda: _async([old_message]))
    meta = run(gm._refresh())                          # ...checked right now
    assert round(gm.SYNC["items"][0]["age_hours"]) == 72
    synced_secs = time.time() - _epoch(meta["last_successful_sync"])
    assert synced_secs < 60, "sync age must reflect the check, not the mail"


# ---- schedule + manual refresh -----------------------------------------

def test_refresh_interval_is_six_hours():
    assert gm.REFRESH_SECONDS == 21600


def test_next_refresh_is_one_interval_after_success(monkeypatch):
    monkeypatch.setattr(gm, "_fetch_inbox", lambda: _async([_msg()]))
    meta = run(gm._refresh())
    delta = _epoch(meta["next_refresh_at"]) - _epoch(meta["last_successful_sync"])
    assert delta == 21600


def test_manual_refresh_is_debounced(monkeypatch):
    monkeypatch.setattr(gm, "_fetch_inbox", lambda: _async([_msg()]))
    first = run(gm.refresh_now())
    assert first["refreshed"] is True
    second = run(gm.refresh_now())
    assert second["refreshed"] is False
    assert second["retry_after_seconds"] > 0


# ---- persistence across restarts ---------------------------------------

def test_state_survives_a_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(gm, "_fetch_inbox", lambda: _async([_msg("persisted")]))
    run(gm._refresh())
    saved = json.loads(Path(gm.STATE_FILE).read_text())
    assert saved["items"][0]["id"] == "persisted"

    gm.SYNC.update(items=[], message_count=0, sync_status="never")  # "restart"
    gm._load_state()
    assert gm.SYNC["items"][0]["id"] == "persisted"
    assert gm.SYNC["last_successful_sync"]


# ---- read-only ----------------------------------------------------------

def test_service_never_mutates_mail():
    src = Path(gm.__file__).read_text()
    for danger in ("/modify", "/trash", "/send", "batchModify", "users/me/messages/send"):
        assert danger not in src, f"mutating Gmail call present: {danger}"


def _async(value):
    async def _c():
        return value
    return _c()


def _epoch(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()


# --- what the credential is actually allowed to do --------------------------
#
# Gmail and Calendar share one OAuth consent here: the schedule service borrows
# this service's token rather than running a second flow. That works right up
# until the token predates the day calendar.events was added to SCOPES — which
# is exactly the case when GOOGLE_REFRESH_TOKEN is set from the PowerBuy app,
# the setup docs' recommended fast path. A refresh token keeps the grant it was
# minted with forever, so Gmail keeps working, Calendar 403s forever, and
# nothing anywhere could tell the difference. Google reports the granted scopes
# on every refresh; these lock down that we read and pass them on.

CAL = "https://www.googleapis.com/auth/calendar.events"
MAIL = "https://www.googleapis.com/auth/gmail.modify"


def test_scopes_are_unknown_until_google_has_told_us(monkeypatch):
    """Three states, not two. Reporting "no calendar access" before a token has
    ever been minted would put a reconnect banner over a healthy connection."""
    monkeypatch.setattr(gm, "_ACCESS", {})
    assert gm.granted_scopes() == []
    assert gm.has_calendar_scope() is None


def test_a_mail_only_credential_is_reported_as_such(monkeypatch):
    """The live failure: PowerBuy's token, reused here, carries gmail.modify
    alone."""
    monkeypatch.setattr(gm, "_ACCESS", {"token": "t", "scope": MAIL})
    assert gm.granted_scopes() == [MAIL]
    assert gm.has_calendar_scope() is False


def test_a_re_consented_credential_reports_calendar_access(monkeypatch):
    monkeypatch.setattr(gm, "_ACCESS", {"token": "t", "scope": f"{MAIL} {CAL}"})
    assert gm.has_calendar_scope() is True
    assert set(gm.granted_scopes()) == {MAIL, CAL}
    # Order in Google's reply is not guaranteed; the answer must not depend on it.
    monkeypatch.setattr(gm, "_ACCESS", {"token": "t", "scope": f"{CAL} {MAIL}"})
    assert gm.has_calendar_scope() is True


def test_scope_parsing_survives_whatever_google_sends(monkeypatch):
    for raw in ("", None, "   ", "  " + MAIL + "   "):
        monkeypatch.setattr(gm, "_ACCESS", {"token": "t", "scope": raw})
        assert all(s for s in gm.granted_scopes()), raw
        assert gm.has_calendar_scope() in (None, False), raw


def test_a_refresh_records_the_scopes_it_was_granted(monkeypatch):
    """The scopes have to be captured where the token is, or they go stale the
    moment a refresh returns a narrower grant than the last one."""
    monkeypatch.setattr(gm, "_ACCESS", {})

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "fresh", "expires_in": 3600, "scope": f"{MAIL} {CAL}"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(gm.httpx, "AsyncClient", FakeClient)
    assert asyncio.run(gm._access_token()) == "fresh"
    assert gm.has_calendar_scope() is True


# --- finishing the connection from a browser that isn't on the box ----------
#
# Google only accepts an http:// redirect URI whose host is localhost or
# 127.0.0.1 — a LAN address is rejected in the Cloud console — so the callback
# can only ever be pointed at localhost. In a browser on the OptiPlex that is
# fine. From a laptop it means Google sends YOUR machine to localhost:8083,
# where nothing is listening, and the connection dies on a "site can't be
# reached" page with the authorization code sitting in the address bar.
#
# /auth/finish takes that address. These lock down that it reads a code out of
# whatever gets pasted, and that a pasted code produces the same saved
# credential a redirect would have.

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def connect_client(monkeypatch, tmp_path):
    monkeypatch.setattr(gm, "CLIENT_ID", "id")
    monkeypatch.setattr(gm, "CLIENT_SECRET", "secret")
    monkeypatch.setattr(gm, "TOKEN_FILE", str(tmp_path / "token.json"))
    monkeypatch.setattr(gm, "TOKENS", {})
    monkeypatch.setattr(gm, "_ACCESS", {})
    return TestClient(gm.app)


def test_the_connect_page_offers_the_rescue_before_anything_breaks():
    """The paste box has to be on the page BEFORE Google is visited. Once the
    callback fails there is no link left to follow — the browser is sitting on
    an error page that belongs to no site at all."""
    c = TestClient(gm.app)
    body = c.get("/auth/login").text
    assert "accounts.google.com" in body       # the consent link
    assert 'action="finish"' in body           # and the way back from a dead end
    assert "site can&#x27;t be reached" in body or "site can't be reached" in body


def test_the_connect_page_uses_a_relative_form_action():
    """It is served both directly (:8083/auth/login) and through the gateway
    (/api/gmail/auth/login). A relative action resolves correctly under both;
    an absolute one would post to the wrong service in one of them."""
    body = TestClient(gm.app).get("/auth/login").text
    assert 'action="finish"' in body
    assert 'action="/auth/finish"' not in body


@pytest.mark.parametrize("pasted,expected", [
    ("http://localhost:8083/auth/callback?code=4/0AbCd-eF&scope=x", "4/0AbCd-eF"),
    ("http://localhost:8083/auth/callback?state=1&code=4%2Fenc", "4/enc"),
    ("code=4/xyz&scope=a", "4/xyz"),          # just the query string
    ("4/barecode", "4/barecode"),             # just the code
    ("   4/whitespace   ", "4/whitespace"),
])
def test_a_code_is_found_in_whatever_gets_pasted(pasted, expected):
    assert gm._extract_code(pasted) == expected


@pytest.mark.parametrize("pasted", ["", "   ", None, "nonsense=1", "http://localhost:8083/"])
def test_nothing_is_invented_when_there_is_no_code(pasted):
    """Guessing here would send junk to Google and report a confusing failure
    instead of "paste the whole address"."""
    assert gm._extract_code(pasted) is None


def _stub_token(monkeypatch, payload, status=200):
    class R:
        status_code = status
        text = json.dumps(payload)

        @staticmethod
        def json():
            return payload

    class C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def post(self, *a, **k):
            C.sent = k.get("data", {})
            return R()

    monkeypatch.setattr(gm.httpx, "AsyncClient", C)
    return C


def test_a_pasted_address_connects_the_account(connect_client, monkeypatch):
    """The whole point: a token obtained by pasting is exactly as good as one
    that arrived by redirect — same exchange, same save."""
    sent = _stub_token(monkeypatch, {
        "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
        "scope": f"{MAIL} {CAL}"})
    r = connect_client.post(
        "/auth/finish",
        data={"pasted": "http://localhost:8083/auth/callback?code=4/real&scope=x"})
    assert r.status_code == 200
    assert "connected" in r.text.lower()
    assert sent.sent["code"] == "4/real"
    assert sent.sent["redirect_uri"] == gm.REDIRECT_URI  # must match the auth request
    assert gm.TOKENS["refresh_token"] == "rt"            # and it is persisted
    assert gm.has_calendar_scope() is True


def test_connecting_without_the_calendar_scope_says_so_rather_than_celebrating(
        connect_client, monkeypatch):
    """Unticking the calendar permission is silent otherwise, and produces
    exactly the dead calendar this whole change exists to fix."""
    _stub_token(monkeypatch, {"access_token": "at", "refresh_token": "rt",
                              "expires_in": 3600, "scope": MAIL})
    r = connect_client.post("/auth/finish", data={"pasted": "4/mailonly"})
    assert "did not" in r.text and "calendar" in r.text.lower()
    assert gm.has_calendar_scope() is False


def test_an_expired_code_explains_itself_and_offers_another_go(
        connect_client, monkeypatch):
    """A code is single-use and short-lived, so this is the failure a real
    person hits most. "invalid_grant" alone tells them nothing."""
    _stub_token(monkeypatch, {"error": "invalid_grant"}, status=400)
    r = connect_client.post("/auth/finish", data={"pasted": "4/stale"})
    assert r.status_code == 200            # a readable page, not a raw 502
    assert "expires" in r.text
    assert 'action="finish"' in r.text     # and the form is still there to retry


def test_junk_in_the_paste_box_is_a_readable_page(connect_client):
    r = connect_client.post("/auth/finish", data={"pasted": "hello"})
    assert r.status_code == 200
    assert "code=" in r.text
    assert 'action="finish"' in r.text


def test_google_refusing_at_the_callback_still_offers_the_paste_box(connect_client):
    r = connect_client.get("/auth/callback", params={"error": "access_denied"})
    assert r.status_code == 200
    assert "access_denied" in r.text
    assert 'action="finish"' in r.text


def test_an_unconfigured_client_says_what_to_set(monkeypatch):
    monkeypatch.setattr(gm, "CLIENT_ID", "")
    body = TestClient(gm.app).get("/auth/login").text
    assert "GOOGLE_CLIENT_ID" in body
    assert "accounts.google.com" not in body  # nothing to send them to yet
