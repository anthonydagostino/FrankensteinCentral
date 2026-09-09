"""What the Google Calendar client is allowed to claim, and how it reads dates.

WHY THIS FILE EXISTS: the dashboard reported the Calendar connection through
`gcal.probe()`, which had three answers — connected, no credential, or
"unreachable" for everything else. The failure this deployment actually has
falls in that third bucket and is not an outage at all: the credential is
borrowed from the gmail service, whose refresh token predates the day
`calendar.events` was added to its scopes (the deployment reuses PowerBuy's
token via GOOGLE_REFRESH_TOKEN). A refresh token keeps the grant it was minted
with forever, so Google answers 403 every time, permanently. Reported as
"unreachable" that reads as "try again later" — advice that can never work.

The date helpers are here for the reason docs/TESTING.md gives: all-day events
are a date-shaped edge that only misbehaves on particular days of the month.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from conftest import load_service_module  # noqa: E402

gcal = load_service_module("schedule_gcal", "services/schedule/app/gcal.py")


def timed(start, end=None):
    ev = {"start": {"dateTime": start}}
    if end:
        ev["end"] = {"dateTime": end}
    return ev


def allday(start, end=None):
    ev = {"start": {"date": start}}
    if end:
        ev["end"] = {"date": end}
    return ev


# --- all-day events stay all-day --------------------------------------------

def test_an_all_day_event_keeps_no_clock_time():
    """A bare date used to be padded to "...T00:00:00", which made a birthday
    indistinguishable from something that genuinely starts at midnight. The
    dashboard decides "all day" by the absence of a time component, so every
    all-day event was being filed under Morning and labelled "12 AM"."""
    assert gcal._event_start(allday("2026-08-01")) == "2026-08-01"
    assert "T" not in gcal._event_start(allday("2026-08-01"))


def test_a_timed_event_keeps_its_offset_untouched():
    """The offset is the only thing stopping an evening event from moving a
    day, so it is passed through exactly as Google sent it."""
    assert gcal._event_start(timed("2026-08-01T21:30:00-04:00")) == "2026-08-01T21:30:00-04:00"


def test_an_event_with_no_usable_start_is_dropped_not_guessed():
    assert gcal._event_start({}) is None
    assert gcal._event_start({"start": {}}) is None


# --- Google's exclusive all-day end -----------------------------------------

def test_a_one_day_all_day_event_does_not_spill_into_tomorrow():
    """Google's all-day `end.date` is EXCLUSIVE: a single day on the 31st is
    sent as end "2026-11-01". Carried through untouched it stretches every
    all-day event into the following column."""
    ev = allday("2026-10-31", "2026-11-01")
    assert gcal._event_end(ev, "2026-10-31") is None  # same day: no range


def test_a_multi_day_all_day_event_ends_on_its_last_real_day():
    ev = allday("2026-10-29", "2026-11-01")
    assert gcal._event_end(ev, "2026-10-29") == "2026-10-31"


def test_all_day_ends_hold_across_a_calendar_sweep():
    """Month ends, year ends and the leap day are exactly where an
    exclusive-end off-by-one shows up, so it is swept rather than spot-checked."""
    for offset in range(900):
        day = date(2026, 1, 1) + timedelta(days=offset)
        start = day.isoformat()
        # A one-day event: the exclusive end is always the next calendar day.
        one_day = allday(start, (day + timedelta(days=1)).isoformat())
        assert gcal._event_end(one_day, start) is None, start
        # A three-day event ends on its third day, never its fourth.
        three = allday(start, (day + timedelta(days=3)).isoformat())
        assert gcal._event_end(three, start) == (day + timedelta(days=2)).isoformat(), start


def test_a_timed_end_is_passed_straight_through():
    ev = timed("2026-08-01T14:00:00-04:00", "2026-08-01T15:00:00-04:00")
    assert gcal._event_end(ev, "2026-08-01T14:00:00-04:00") == "2026-08-01T15:00:00-04:00"


def test_a_missing_or_unparseable_end_is_none_not_a_guess():
    assert gcal._event_end({"start": {"date": "2026-08-01"}}, "2026-08-01") is None
    assert gcal._event_end(allday("2026-08-01", "not-a-date"), "2026-08-01") is None


# --- probe: four states, because they need four different repairs -----------

class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code

    def json(self):
        return {"items": []}


class FakeClient:
    """Enough of httpx.AsyncClient for probe(); records that it was called."""
    calls = 0
    status = 200
    raises = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *args, **kwargs):
        FakeClient.calls += 1
        if FakeClient.raises:
            raise RuntimeError("connection reset")
        return FakeResponse(FakeClient.status)


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeClient.calls = 0
    FakeClient.status = 200
    FakeClient.raises = False


def run(coro):
    import asyncio
    return asyncio.run(coro)


def with_credential(monkeypatch, cred):
    async def _credential():
        return cred
    monkeypatch.setattr(gcal, "_credential", _credential)
    monkeypatch.setattr(gcal.httpx, "AsyncClient", FakeClient)


def test_no_credential_at_all_is_disconnected(monkeypatch):
    with_credential(monkeypatch, None)
    assert run(gcal.probe()) == "disconnected"
    assert FakeClient.calls == 0  # nothing to ask with; don't pretend to try


def test_a_credential_without_the_calendar_scope_needs_consent(monkeypatch):
    """The exact live failure. gmail reports the scopes Google granted, so
    this is settled before a single API call is spent on it."""
    with_credential(monkeypatch, {"access_token": "t", "calendar_scope": False,
                                  "scopes": ["https://www.googleapis.com/auth/gmail.modify"]})
    assert run(gcal.probe()) == "needs_consent"
    assert FakeClient.calls == 0


def test_a_refusal_from_google_needs_consent_not_a_retry(monkeypatch):
    """401/403 mean the credential exists and Calendar will not honour it.
    That is permanent until someone re-consents; it is not an outage."""
    for code in (401, 403):
        FakeClient.status = code
        with_credential(monkeypatch, {"access_token": "t", "calendar_scope": None})
        assert run(gcal.probe()) == "needs_consent", code


def test_a_real_outage_is_still_unreachable(monkeypatch):
    with_credential(monkeypatch, {"access_token": "t", "calendar_scope": True})
    FakeClient.raises = True
    assert run(gcal.probe()) == "unreachable"
    FakeClient.raises = False
    FakeClient.status = 500
    assert run(gcal.probe()) == "unreachable"


def test_a_working_calendar_is_ok(monkeypatch):
    with_credential(monkeypatch, {"access_token": "t", "calendar_scope": True})
    assert run(gcal.probe()) == "ok"
    assert FakeClient.calls == 1  # "ok" is never claimed without asking


def test_unestablished_scopes_do_not_short_circuit(monkeypatch):
    """`calendar_scope: None` means gmail has not minted a token yet, which is
    evidence of nothing. Treating it as False would put a reconnect banner in
    front of a connection that is fine."""
    with_credential(monkeypatch, {"access_token": "t", "calendar_scope": None})
    assert run(gcal.probe()) == "ok"
    assert FakeClient.calls == 1


# --- pushing back out: the same shapes, in reverse ---------------------------

def test_a_timed_event_is_pushed_as_a_datetime():
    body = gcal._body("Standup", "2026-08-01T09:30:00-04:00",
                      "2026-08-01T09:45:00-04:00", "confirmed")
    assert body["start"] == {"dateTime": "2026-08-01T09:30:00-04:00"}
    assert body["end"] == {"dateTime": "2026-08-01T09:45:00-04:00"}


def test_an_all_day_event_is_pushed_as_a_date_google_will_accept():
    """`{"dateTime": "2026-10-31"}` is rejected outright, and a start equal to
    an all-day end is too — Google's all-day end is exclusive, so a one-day
    event has to be sent ending the following day. That is the mirror of the
    pull-back _event_end does on the way in, and the round trip has to land
    back where it started."""
    body = gcal._body("Mom's birthday", "2026-10-31", None, "confirmed")
    assert body["start"] == {"date": "2026-10-31"}
    assert body["end"] == {"date": "2026-11-01"}
    # Round trip: what we send, read back, is the day we started with.
    sent = {"start": body["start"], "end": body["end"]}
    assert gcal._event_start(sent) == "2026-10-31"
    assert gcal._event_end(sent, "2026-10-31") is None  # same day, no range


def test_the_all_day_round_trip_holds_across_a_calendar_sweep():
    for offset in range(900):
        day = (date(2026, 1, 1) + timedelta(days=offset)).isoformat()
        body = gcal._body("All-dayer", day, None, "confirmed")
        sent = {"start": body["start"], "end": body["end"]}
        assert gcal._event_start(sent) == day, day
        assert gcal._event_end(sent, day) is None, day


def test_a_multi_day_all_day_event_survives_the_round_trip():
    body = gcal._body("Conference", "2026-10-29", "2026-10-31", "confirmed")
    assert body["end"] == {"date": "2026-11-01"}
    sent = {"start": body["start"], "end": body["end"]}
    assert gcal._event_end(sent, "2026-10-29") == "2026-10-31"
