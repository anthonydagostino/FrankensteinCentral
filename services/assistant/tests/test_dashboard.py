"""What the home screen is allowed to claim it knows.

WHY THIS FILE EXISTS: the dashboard had no calendar, and the one line that
mentioned an event — "🗓️ Next:" at the bottom of the Big 3 card — showed the
OLDEST event on record. `GET /events` returns every event ever stored, ordered
ascending with no lower bound, and `next_event` was `events[0]`. So the "next"
event was the first one ever created, and `_do_next`'s "starts in 30 min" rules
could never fire, because the minutes-until figure was enormously negative.

Per `docs/TESTING.md`, the date-sensitive assertions here never trust today's
date: they sweep a two-year calendar and assert the invariants that must hold
on every one of those days. The original money-layer outage only appeared on
the 1st of a month; a test that runs on the day it was written cannot catch a
bug that waits for a date.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

dash = load_service_module("assistant_dashboard",
                           "services/assistant/app/dashboard.py")

NY = ZoneInfo("America/New_York")


def ev(title, start, status="confirmed", end=None):
    e = {"title": title, "starts_at": start, "status": status}
    if end:
        e["ends_at"] = end
    return e


# --- Acceptance criterion 4: the issue #3 regression -------------------------

def test_a_past_event_never_wins_over_a_future_one():
    """The named regression: given yesterday and tomorrow, show tomorrow."""
    now = datetime(2026, 9, 6, 12, 0, tzinfo=NY)
    events = [
        ev("Old standup", "2025-01-04T09:00:00"),   # oldest — was chosen
        ev("Interview", "2026-09-07T14:00:00"),     # actually next
    ]
    got = dash.upcoming_events(events, now, NY)
    assert [e["title"] for e in got] == ["Interview"]


def test_the_oldest_record_is_not_the_next_event_on_any_day_of_two_years():
    """Sweep: an ancient event must never be reported as upcoming, whatever
    today happens to be."""
    ancient = ev("Ancient", "2019-03-02T08:00:00")
    day = datetime(2026, 1, 1, 9, 0, tzinfo=NY)
    for _ in range(730):
        soon = ev("Soon", (day + timedelta(days=1)).replace(hour=10).isoformat())
        got = dash.upcoming_events([ancient, soon], day, NY)
        assert [e["title"] for e in got] == ["Soon"], f"failed on {day.date()}"
        day += timedelta(days=1)


def test_events_come_back_soonest_first_regardless_of_input_order():
    now = datetime(2026, 9, 6, 8, 0, tzinfo=NY)
    events = [
        ev("Third", "2026-09-09T09:00:00"),
        ev("First", "2026-09-06T09:00:00"),
        ev("Second", "2026-09-07T09:00:00"),
    ]
    assert [e["title"] for e in dash.upcoming_events(events, now, NY)] == [
        "First", "Second", "Third"]


def test_an_event_in_progress_is_still_upcoming():
    """It should not vanish from the page halfway through."""
    now = datetime(2026, 9, 6, 14, 30, tzinfo=NY)
    events = [ev("Interview", "2026-09-06T14:00:00", end="2026-09-06T15:00:00")]
    assert len(dash.upcoming_events(events, now, NY)) == 1


def test_an_event_that_has_ended_is_dropped():
    now = datetime(2026, 9, 6, 16, 0, tzinfo=NY)
    events = [ev("Interview", "2026-09-06T14:00:00", end="2026-09-06T15:00:00")]
    assert dash.upcoming_events(events, now, NY) == []


# --- pending and countered holds reach the page ------------------------------

def test_pending_and_countered_holds_are_shown_by_default():
    """The Gmail->Cal pipeline's whole output used to be filtered away."""
    now = datetime(2026, 9, 6, 8, 0, tzinfo=NY)
    events = [
        ev("Offered slot", "2026-09-07T09:00:00", status="pending"),
        ev("They countered", "2026-09-08T09:00:00", status="countered"),
        ev("Locked in", "2026-09-09T09:00:00"),
    ]
    got = dash.upcoming_events(events, now, NY)
    assert {e["status"] for e in got} == {"pending", "countered", "confirmed"}


def test_confirmed_only_filter_still_available_for_the_rules():
    now = datetime(2026, 9, 6, 8, 0, tzinfo=NY)
    events = [
        ev("Offered slot", "2026-09-07T09:00:00", status="pending"),
        ev("Locked in", "2026-09-09T09:00:00"),
    ]
    got = dash.upcoming_events(events, now, NY, statuses=("confirmed",))
    assert [e["title"] for e in got] == ["Locked in"]


# --- timezone handling -------------------------------------------------------

def test_a_naive_evening_timestamp_is_local_not_utc():
    """Reading a naive 9pm as UTC would push an evening event to tomorrow —
    the same failure `docs/TESTING.md` documents for the money layer."""
    parsed = dash.parse_event_dt("2026-09-06T21:00:00", NY)
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (2026, 9, 6, 21)
    assert parsed.tzinfo is not None


def test_an_offset_timestamp_is_converted_not_relabelled():
    parsed = dash.parse_event_dt("2026-09-06T21:00:00+00:00", NY)
    assert parsed.hour == 17  # 21:00 UTC is 17:00 EDT


@pytest.mark.parametrize("bad", ["", None, "not a date", "2026-13-45T99:00:00"])
def test_unparseable_timestamps_are_dropped_not_guessed(bad):
    assert dash.parse_event_dt(bad, NY) is None
    assert dash.upcoming_events([ev("X", bad)], datetime.now(NY), NY) == []


def test_the_limit_is_respected():
    now = datetime(2026, 9, 6, 8, 0, tzinfo=NY)
    events = [ev(f"E{i}", f"2026-09-{7 + i:02d}T09:00:00") for i in range(10)]
    assert len(dash.upcoming_events(events, now, NY, limit=6)) == 6
    assert len(dash.upcoming_events(events, now, NY, limit=None)) == 10


# --- Acceptance criterion 6: unreachable is not unconfigured -----------------

def test_an_unreachable_firefly_is_not_reported_as_unconfigured():
    """`_get` returns {} on a timeout. That must not read as 'set FIREFLY_URL',
    which sends you to fix configuration that is already correct."""
    assert dash.firefly_state({}) == "unreachable"
    assert dash.firefly_state(None) == "unreachable"


def test_firefly_saying_it_has_no_credentials_is_unconfigured():
    assert dash.firefly_state({"connected": False}) == "not_configured"


def test_a_working_firefly_is_ok():
    assert dash.firefly_state({"connected": True, "net_worth": {}}) == "ok"
    # A payload with no explicit flag is not evidence of a missing credential.
    assert dash.firefly_state({"net_worth": {}}) == "ok"
