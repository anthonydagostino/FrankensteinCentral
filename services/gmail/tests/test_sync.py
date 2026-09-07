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
