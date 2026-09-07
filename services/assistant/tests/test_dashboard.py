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


# --- FC-008: the seven-day week window --------------------------------------
#
# Same discipline as the money layer's date tests: the window is swept across a
# multi-year calendar rather than asserted once against whatever today happens
# to be. A seven-day grid that is correct in June and wrong on 29 February is
# the exact failure mode `docs/TESTING.md` exists to stop.

from datetime import date, timedelta  # noqa: E402

SWEEP_START = date(2026, 1, 1)
SWEEP_DAYS = 800  # two years and change: both DST pairs, two Feb 29s nearby


def sweep_dates():
    return (SWEEP_START + timedelta(days=n) for n in range(SWEEP_DAYS))


def at(d, hour=9, minute=0):
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=NY)


# --- ordinals ---------------------------------------------------------------

def test_ordinal_suffix_handles_the_teens():
    """11th/12th/13th, not 11st/12nd/13rd — the whole reason for the branch."""
    assert dash.ordinal(11) == "11th"
    assert dash.ordinal(12) == "12th"
    assert dash.ordinal(13) == "13th"
    assert dash.ordinal(1) == "1st"
    assert dash.ordinal(2) == "2nd"
    assert dash.ordinal(3) == "3rd"
    assert dash.ordinal(21) == "21st"
    assert dash.ordinal(22) == "22nd"
    assert dash.ordinal(23) == "23rd"
    assert dash.ordinal(31) == "31st"


def test_every_day_of_the_month_gets_the_right_suffix():
    expected = {1: "st", 2: "nd", 3: "rd", 21: "st", 22: "nd", 23: "rd", 31: "st"}
    for day in range(1, 32):
        assert dash.ordinal_suffix(day) == expected.get(day, "th"), day


# --- the sweep --------------------------------------------------------------

def test_window_is_always_seven_consecutive_days_starting_today():
    """Acceptance criterion 1, asserted on every day of a two-year calendar."""
    for d in sweep_dates():
        week = dash.week_window([], at(d), NY)
        days = week["days"]
        assert len(days) == 7, d
        assert days[0]["iso"] == d.isoformat(), d
        assert days[0]["is_today"] is True, d
        assert days[1]["is_tomorrow"] is True, d
        # Consecutive, no gaps, no repeats — including across DST and leap day.
        seen = [date.fromisoformat(x["iso"]) for x in days]
        assert seen == [d + timedelta(days=n) for n in range(7)], d
        assert len(set(seen)) == 7, d
        # No past day ever appears.
        assert all(x >= d for x in seen), d


def test_labels_match_the_real_calendar_on_every_swept_day():
    for d in sweep_dates():
        for cell in dash.week_window([], at(d), NY)["days"]:
            real = date.fromisoformat(cell["iso"])
            assert cell["weekday"] == real.strftime("%A"), cell
            assert cell["month"] == real.strftime("%B"), cell
            assert cell["day"] == real.day, cell
            assert cell["year"] == real.year, cell
            assert cell["ordinal"] == f"{real.day}{dash.ordinal_suffix(real.day)}"
            assert cell["is_weekend"] == (real.weekday() >= 5), cell
            assert cell["long_label"].startswith(cell["weekday"])
            assert cell["ordinal"] in cell["long_label"]


def test_month_boundaries_are_labelled_not_hidden():
    """A window spanning two months says so; day numbers never silently reset."""
    for d in sweep_dates():
        days = dash.week_window([], at(d), NY)["days"]
        assert days[0]["starts_month"] is True, d
        flagged = [c for c in days[1:] if c["starts_month"]]
        crossings = [c for i, c in enumerate(days[1:], 1)
                     if c["month_index"] != days[i - 1]["month_index"]]
        assert flagged == crossings, d
        assert (len({c["month_index"] for c in days}) > 1) == \
            dash.week_window([], at(d), NY)["spans_months"], d


def test_leap_day_is_present_and_correctly_labelled():
    week = dash.week_window([], at(date(2028, 2, 26)), NY)
    isos = [c["iso"] for c in week["days"]]
    assert "2028-02-29" in isos
    leap = next(c for c in week["days"] if c["iso"] == "2028-02-29")
    assert leap["ordinal"] == "29th"
    assert leap["month"] == "February"
    assert leap["weekday"] == "Tuesday"
    # And the day after a leap day is 1 March, not 30 February.
    assert isos[isos.index("2028-02-29") + 1] == "2028-03-01"


@pytest.mark.parametrize("start", [
    date(2026, 3, 6),   # US spring forward: 23-hour day on the 8th
    date(2026, 10, 30),  # US fall back: 25-hour day on 1 Nov
    date(2027, 3, 12),
    date(2027, 11, 5),
])
def test_dst_days_do_not_shift_the_grid(start):
    """A 23- or 25-hour day must not slide events into a neighbouring column.

    This is the reason bucketing is done on local dates instead of by adding
    86400-second offsets to a timestamp.
    """
    expected = [(start + timedelta(days=n)).isoformat() for n in range(7)]

    # Every hour, not just office hours. An absolute-offset implementation
    # (UTC + 86400s) produces the right dates all day and only breaks within
    # an hour of midnight, so a 9am-only assertion is decoration: it passes
    # against the very bug it claims to guard.
    for hour in range(24):
        days = dash.week_window([], at(start, hour, 30), NY)["days"]
        assert [c["iso"] for c in days] == expected, (start, hour)

    # An 8pm commitment on each day lands on that day, never the one before.
    events = [ev(f"Dinner {n}", at(start + timedelta(days=n), 20).isoformat())
              for n in range(7)]
    week = dash.week_window(events, at(start, 0, 30), NY)
    for n, cell in enumerate(week["days"]):
        assert [e["title"] for e in cell["events"]] == [f"Dinner {n}"], cell["iso"]


# --- what the card is allowed to claim --------------------------------------

def test_pending_and_countered_holds_survive_the_new_layout():
    """Regression: dropping non-confirmed holds was a fixed bug. It stays fixed.

    An interview slot awaiting a reply is the single most actionable thing the
    pipeline produces; the week grid must not filter it out the way the old
    pre-fix card did.
    """
    d = date(2026, 10, 1)
    events = [
        ev("Confirmed standup", at(d, 9).isoformat()),
        ev("Offered interview", at(d, 11).isoformat(), status="pending"),
        ev("They countered", at(d, 15).isoformat(), status="countered"),
        ev("Declined slot", at(d, 17).isoformat(), status="declined"),
    ]
    today = dash.week_window(events, at(d, 7), NY)["days"][0]
    titles = [e["title"] for e in today["events"]]
    assert "Offered interview" in titles
    assert "They countered" in titles
    assert "Declined slot" not in titles
    assert today["counts"] == {"total": 3, "confirmed": 1, "pending": 1,
                               "countered": 1, "needs_you": 1}
    countered = next(e for e in today["events"] if e["status"] == "countered")
    assert countered["needs_you"] is True


def test_overlapping_commitments_are_flagged_as_conflicts():
    d = date(2026, 11, 3)
    events = [
        ev("Dentist", at(d, 10).isoformat(), end=at(d, 11).isoformat()),
        ev("Standup", at(d, 10, 30).isoformat(), end=at(d, 11, 30).isoformat()),
        ev("Quiet block", at(d, 14).isoformat(), end=at(d, 15).isoformat()),
    ]
    today = dash.week_window(events, at(d, 8), NY)["days"][0]
    assert today["conflicts"] == 2
    flagged = {e["title"] for e in today["events"] if e["conflict"]}
    assert flagged == {"Dentist", "Standup"}


def test_two_zero_length_events_at_the_same_instant_conflict():
    """A plain interval test misses this: both are points, so neither contains
    the other. Two things at 9am is still two things at 9am."""
    d = date(2026, 11, 3)
    events = [ev("Call A", at(d, 9).isoformat()), ev("Call B", at(d, 9).isoformat())]
    today = dash.week_window(events, at(d, 8), NY)["days"][0]
    assert today["conflicts"] == 2


def test_an_event_in_progress_stays_on_today():
    d = date(2026, 6, 10)
    events = [ev("Long workshop", at(d, 9).isoformat(), end=at(d, 17).isoformat())]
    today = dash.week_window(events, at(d, 13), NY)["days"][0]
    assert [e["title"] for e in today["events"]] == ["Long workshop"]
    assert today["events"][0]["ongoing"] is True


def test_an_event_that_started_yesterday_and_still_runs_shows_on_today():
    d = date(2026, 6, 10)
    events = [ev("Overnight trip",
                 at(d - timedelta(days=1), 20).isoformat(),
                 end=at(d, 11).isoformat())]
    week = dash.week_window(events, at(d, 9), NY)
    assert [e["title"] for e in week["days"][0]["events"]] == ["Overnight trip"]
    assert week["days"][0]["events"][0]["ongoing"] is True


def test_finished_and_stale_events_do_not_appear():
    d = date(2026, 6, 10)
    events = [
        ev("This morning", at(d, 7).isoformat(), end=at(d, 8).isoformat()),
        ev("Last week", at(d - timedelta(days=7), 9).isoformat()),
    ]
    week = dash.week_window(events, at(d, 12), NY)
    assert all(not c["events"] for c in week["days"])


def test_events_past_the_window_are_counted_not_dropped():
    """Silently discarding them would make a busy fortnight look like a free one."""
    d = date(2026, 6, 10)
    events = [ev("Far future", at(d + timedelta(days=n), 9).isoformat())
              for n in (7, 8, 20)]
    week = dash.week_window(events, at(d, 8), NY)
    assert week["beyond"] == 3
    assert all(not c["events"] for c in week["days"])


def test_all_day_events_are_not_filed_under_a_clock_time():
    d = date(2026, 10, 31)
    events = [ev("Halloween", d.isoformat()),
              ev("Morning alarm", at(d, 7).isoformat())]
    today = dash.week_window(events, at(d, 6), NY)["days"][0]
    allday = next(e for e in today["events"] if e["title"] == "Halloween")
    assert allday["all_day"] is True
    assert allday["slot"] == "allday"
    assert allday["time_label"] == "All day"
    # All-day markers are context, not a competing obligation.
    assert today["conflicts"] == 0
    # ...and they sort above the timed events.
    assert today["events"][0]["title"] == "Halloween"


def test_time_labels_drop_the_zero_minutes():
    d = date(2026, 6, 10)
    events = [ev("On the hour", at(d, 9).isoformat(), end=at(d, 10, 30).isoformat()),
              ev("Half past", at(d, 21, 30).isoformat())]
    cell = dash.week_window(events, at(d, 8), NY)["days"][0]
    on_hour = next(e for e in cell["events"] if e["title"] == "On the hour")
    half = next(e for e in cell["events"] if e["title"] == "Half past")
    assert on_hour["time_label"] == "9 AM"
    assert on_hour["end_label"] == "10:30 AM"
    assert half["time_label"] == "9:30 PM"


def test_slots_split_the_day_into_readable_parts():
    d = date(2026, 6, 10)
    events = [ev("Dawn", at(d, 6).isoformat()), ev("Lunch", at(d, 12).isoformat()),
              ev("Dinner", at(d, 19).isoformat())]
    cell = dash.week_window(events, at(d, 5), NY)["days"][0]
    assert [e["slot"] for e in cell["events"]] == ["morning", "afternoon", "evening"]


def test_unparseable_timestamps_are_dropped_not_guessed_at():
    d = date(2026, 6, 10)
    events = [ev("Nonsense", "not a date"), ev("Real", at(d, 9).isoformat())]
    cell = dash.week_window(events, at(d, 8), NY)["days"][0]
    assert [e["title"] for e in cell["events"]] == ["Real"]


# --- honest states ----------------------------------------------------------

LIVE = {"mode": "live"}
CAL_OK = {"state": "ok", "ok": True}
CAL_DOWN = {"state": "unreachable", "ok": False}
CAL_NONE = {"state": "disconnected", "ok": False}

# The EXACT shape services/gmail/app/main.py produces when an inbox fetch
# fails but cached items exist: mode stays "live" (serving last-known-good)
# while sync_status flips to "failed". Codex asked that the tests use this
# rather than a synthetic dict, because it is the state that made the old
# mode-only check wrong.
GMAIL_CACHED_FAILURE = {
    "mode": "live",
    "emails": [{"id": "cached"}],
    "sync": {"sync_status": "failed", "last_error": "Gmail fetch failed",
             "last_successful_sync": "2026-09-06T12:00:00Z"},
}
GMAIL_HEALTHY = {
    "mode": "live",
    "emails": [{"id": "fresh"}],
    "sync": {"sync_status": "healthy", "last_error": None,
             "last_successful_sync": "2026-09-07T12:00:00Z"},
}


def test_an_unreachable_schedule_service_is_not_an_empty_week():
    """Vision principle 1. `_get` returns {} on a timeout; rendering that as
    "nothing coming up" is the calmest possible way to hide real commitments."""
    assert dash.schedule_state({}, LIVE, CAL_OK) == "unreachable"
    assert dash.schedule_state(None, LIVE, CAL_OK) == "unreachable"
    assert dash.schedule_state({"events": []}, LIVE, CAL_OK) == "ok"
    assert dash.schedule_state({"events": [1]}, LIVE, CAL_OK) == "ok"


def test_a_disconnected_calendar_is_not_a_healthy_empty_week():
    """The bug Codex found reviewing add5b2b: a truthy payload returned `ok`,
    so a schedule service answering normally with a DISCONNECTED calendar
    rendered exactly like a genuinely clear week."""
    assert dash.schedule_state({"events": []}, {"mode": "disconnected"}) == "disconnected"
    assert dash.schedule_state({"events": [1]}, {"mode": "disconnected"}) == "disconnected"
    # The shape that reproduced it.
    assert dash.schedule_state({"connected": False, "events": []},
                               {"mode": "disconnected"}) != "ok"
    # And the probe saying "no token" is equally conclusive.
    assert dash.schedule_state({"events": []}, LIVE, CAL_NONE) == "disconnected"


def test_gmail_live_while_its_own_sync_failed_is_never_healthy():
    """Codex's second P1, reproduced with the real shape.

    services/gmail/app/main.py KEEPS mode="live" when a fetch fails but cached
    items exist, setting sync_status="failed" beside it. "live" there means
    "still serving last-known-good mail", not "the connection works" — so it
    must never, on its own, turn an unconfirmed Calendar into a clear week.
    """
    assert dash.schedule_state({"events": []}, GMAIL_CACHED_FAILURE) == "unknown"
    assert dash.schedule_state({"events": [1]}, GMAIL_CACHED_FAILURE) == "unknown"
    # Not even with a probe that failed.
    assert dash.schedule_state({"events": []}, GMAIL_CACHED_FAILURE, CAL_DOWN) == "unknown"


def test_a_healthy_inbox_is_not_confirmed_calendar_health():
    """The distinction Codex asked for explicitly. A perfectly healthy Gmail
    sync says nothing about whether Calendar is reachable or in scope: gcal
    borrows the credential, and a shared credential is not an API check."""
    assert dash.schedule_state({"events": []}, GMAIL_HEALTHY) == "unknown"
    assert dash.schedule_state({"events": []}, GMAIL_HEALTHY, CAL_DOWN) == "unknown"
    # Only a Calendar-specific read flips it.
    assert dash.schedule_state({"events": []}, GMAIL_HEALTHY, CAL_OK) == "ok"


def test_only_positive_calendar_evidence_reaches_ok():
    """`ok` is reserved for something that actually performed a Calendar read."""
    for link in (None, {}, LIVE, GMAIL_HEALTHY, GMAIL_CACHED_FAILURE,
                 {"mode": "error"}, {"mode": "never"}):
        assert dash.schedule_state({"events": []}, link) != "ok", link
        assert dash.schedule_state({"events": []}, link, {}) != "ok", link
        assert dash.schedule_state({"events": []}, link, CAL_DOWN) != "ok", link
    # A probe that merely ANSWERED is not a probe that succeeded.
    assert dash.schedule_state({"events": []}, LIVE, {"state": "unreachable"}) == "unknown"
    assert dash.schedule_state({"events": []}, LIVE, {"ok": False}) == "unknown"


def test_unestablished_connection_health_is_unknown_not_ok():
    """Absence of evidence is not evidence of health. `never` and `error` are
    not "connected", and an unreachable gmail means we cannot see the
    credential at all — none of those may render as a clear week."""
    for link in ({}, None, {"mode": "error"}, {"mode": "never"},
                 {"mode": "something-added-later"}, {"events": []}):
        assert dash.schedule_state({"events": []}, link) == "unknown", link


def test_connection_evidence_is_never_inferred_from_the_wrapper():
    """A non-empty payload is not a connection. Only the evidence decides."""
    assert dash.schedule_state({"events": [1, 2, 3]}, None) == "unknown"
    assert dash.schedule_state({"events": [1, 2, 3]}, {"mode": "disconnected"}) == "disconnected"


def test_every_state_is_one_of_the_documented_four():
    for sched in ({}, {"events": []}, {"events": [1]}):
        for link in (None, {}, LIVE, GMAIL_CACHED_FAILURE, {"mode": "disconnected"}):
            for ev in (None, {}, CAL_OK, CAL_DOWN, CAL_NONE):
                assert dash.schedule_state(sched, link, ev) in dash.SCHEDULE_STATES


def test_local_commitments_survive_a_disconnected_calendar():
    """Rows already in Postgres are real whatever Google is doing. Hiding the
    week because the integration is down is its own dishonesty."""
    d = date(2026, 6, 10)
    events = [ev("Locally stored dentist", at(d, 10).isoformat())]
    week = dash.week_window(events, at(d, 8), NY)
    assert [e["title"] for e in week["days"][0]["events"]] == ["Locally stored dentist"]
    # The state is a separate fact from the data; it never empties the grid.
    assert dash.schedule_state({"events": events}, {"mode": "disconnected"}) == "disconnected"


def test_an_empty_but_healthy_week_is_distinguishable_from_an_outage():
    d = date(2026, 6, 10)
    healthy = dash.week_window([], at(d), NY)
    assert healthy["beyond"] == 0
    assert all(c["counts"]["total"] == 0 for c in healthy["days"])
    # The grid still renders seven labelled days; emptiness is a fact about the
    # data, and the state field is what says whether that fact is trustworthy.
    assert len(healthy["days"]) == 7


def test_season_key_is_derived_from_the_date_and_nothing_else():
    for d in sweep_dates():
        for cell in dash.week_window([], at(d), NY)["days"]:
            real = date.fromisoformat(cell["iso"])
            assert cell["season"] == dash.SEASON_KEYS[real.month - 1]


def test_conflicts_are_detected_across_midnight():
    """A per-day pass is structurally blind to this: an 11:30pm call and a
    12:15am call are one collision filed under two dates."""
    d = date(2026, 6, 10)
    events = [
        ev("Late call", at(d, 23, 30).isoformat(), end=at(d + timedelta(days=1), 0, 30).isoformat()),
        ev("Overnight page", at(d + timedelta(days=1), 0, 15).isoformat(),
           end=at(d + timedelta(days=1), 1, 0).isoformat()),
        ev("Unrelated", at(d + timedelta(days=1), 9).isoformat()),
    ]
    week = dash.week_window(events, at(d, 8), NY)
    today, tomorrow = week["days"][0], week["days"][1]
    late = next(e for e in today["events"] if e["title"] == "Late call")
    page = next(e for e in tomorrow["events"] if e["title"] == "Overnight page")
    free = next(e for e in tomorrow["events"] if e["title"] == "Unrelated")
    assert late["conflict"] is True
    assert page["conflict"] is True
    assert free["conflict"] is False
    # Both are told the clash is with a different calendar day, and which
    # direction it lies in — "overlaps next day" on a 00:15 event whose other
    # half was the night before is worse than saying nothing.
    assert late["conflict_offday"] is True
    assert page["conflict_offday"] is True
    assert late["conflict_neighbour"] == "next"
    assert page["conflict_neighbour"] == "previous"
    assert today["conflicts"] == 1 and tomorrow["conflicts"] == 1


def test_same_day_conflicts_are_not_marked_as_cross_day():
    d = date(2026, 6, 10)
    events = [
        ev("A", at(d, 10).isoformat(), end=at(d, 11).isoformat()),
        ev("B", at(d, 10, 30).isoformat(), end=at(d, 11, 30).isoformat()),
    ]
    today = dash.week_window(events, at(d, 8), NY)["days"][0]
    assert today["conflicts"] == 2
    assert all(e["conflict_offday"] is False for e in today["events"])
    assert all(e["conflict_neighbour"] is None for e in today["events"])


def test_events_on_different_days_that_do_not_overlap_are_not_conflicts():
    """The window-wide pass must not turn "two busy days" into a conflict."""
    d = date(2026, 6, 10)
    events = [ev(f"Day {n}", at(d + timedelta(days=n), 9).isoformat(),
                 end=at(d + timedelta(days=n), 10).isoformat()) for n in range(7)]
    week = dash.week_window(events, at(d, 8), NY)
    assert all(c["conflicts"] == 0 for c in week["days"])
    assert all(not e["conflict"] for c in week["days"] for e in c["events"])


# --- the weekly review (PRODUCT_IDEAS #5) -----------------------------------

REVIEW = {"study": {"week_min": 300, "last_min": 240, "goal_min": 600},
          "gym": {"week": 3, "goal": 4},
          "water": {"days_hit": 5, "of": 7}}


def test_the_review_leads_on_sunday_evening_and_only_then():
    """Swept across a calendar rather than asserted once: the acceptance
    signal is "it's on the screen on a Sunday", which is a claim about every
    Sunday, not about the day this test was written."""
    for d in sweep_dates():
        for hour in range(24):
            slot = dash.weekly_review_slot(at(d, hour, 30), NY)
            should_lead = (d.weekday() == 6 and hour >= 17)
            assert (slot == "lead") == should_lead, (d, hour, slot)
            assert slot in ("lead", "normal")


def test_the_review_is_never_hidden_on_other_days():
    """It answers "where did the week go" — worth seeing any day. What changes
    is whether it takes the top of the page, not whether it exists."""
    for d in sweep_dates():
        assert dash.weekly_review_slot(at(d, 9), NY) in ("lead", "normal")
        assert dash.weekly_review(REVIEW, at(d, 9), NY) is not None


def test_no_goal_set_is_not_zero_percent_achieved():
    """Idea #16's bug in a different costume: not setting a goal is not
    missing one. The percentage must be absent, not 0."""
    for goal in (0, None):
        row = dash.weekly_progress(5, goal)
        assert row["state"] == "no_goal"
        assert row["pct"] is None
        assert row["value"] == 5


def test_a_missing_figure_is_unknown_not_zero():
    row = dash.weekly_progress(None, 600)
    assert row["state"] == "unknown"
    assert row["pct"] is None
    assert row["value"] is None


def test_progress_states_are_hit_or_under_against_a_real_goal():
    assert dash.weekly_progress(600, 600)["state"] == "hit"
    assert dash.weekly_progress(601, 600)["state"] == "hit"
    assert dash.weekly_progress(599, 600)["state"] == "under"
    assert dash.weekly_progress(300, 600)["pct"] == 50


def test_an_unreachable_core_is_not_a_week_of_zeroes():
    """`_get` returns {} on a timeout. Rendering that as a week where nothing
    happened is the same lie the schedule card was fixed for."""
    for empty in ({}, None):
        assert dash.weekly_review(empty, at(date(2026, 6, 14), 19), NY) is None


def test_the_study_trend_needs_both_figures():
    d = date(2026, 6, 14)
    got = dash.weekly_review(REVIEW, at(d, 19), NY)
    assert got["study"]["trend_min"] == 60          # 300 this week, 240 last
    no_last = {**REVIEW, "study": {"week_min": 300, "goal_min": 600}}
    assert dash.weekly_review(no_last, at(d, 19), NY)["study"]["trend_min"] is None


def test_the_review_survives_a_partial_payload():
    """Core answering with some sections missing must not raise."""
    got = dash.weekly_review({"study": {}}, at(date(2026, 6, 14), 19), NY)
    assert got["study"]["state"] == "unknown"
    assert got["gym"]["state"] == "unknown"
    assert got["water"]["state"] == "unknown"


# --- portfolio: unreachable is not empty (PRODUCT_IDEAS #13) ----------------

def test_an_unreachable_stocks_service_is_not_an_empty_portfolio():
    """The bug: `_get` returns {} on a timeout and the payload collapsed that
    into {"configured": False}, so a briefly-down container told you to "add
    your stocks" — go fix configuration that is already correct."""
    assert dash.portfolio_state({}) == "unreachable"
    assert dash.portfolio_state(None) == "unreachable"


def test_genuinely_unconfigured_stocks_still_says_so():
    assert dash.portfolio_state({"configured": False, "positions": []}) == "not_configured"


def test_a_working_portfolio_is_ok():
    assert dash.portfolio_state({"configured": True, "positions": [1]}) == "ok"
    # Configured with nothing held yet is still reachable, not an outage.
    assert dash.portfolio_state({"configured": True, "positions": []}) == "ok"


def test_portfolio_mirrors_the_firefly_contract():
    """firefly_state is the pattern this repo already got right; #13 is about
    making it universal. The two must not drift apart."""
    for payload in ({}, None, {"connected": False}, {"connected": True}):
        assert dash.firefly_state(payload) in ("ok", "unreachable", "not_configured")
    assert dash.firefly_state({}) == dash.portfolio_state({}) == "unreachable"
