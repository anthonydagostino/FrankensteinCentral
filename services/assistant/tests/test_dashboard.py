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


# ══ PRODUCT_IDEAS #4 — "since you last checked" follows the person ════════
#
# It lived in localStorage under `cc_snap`, so a phone, two MacBooks, a Kali
# laptop and the OptiPlex each kept a different answer, and a fresh browser had
# none. The baseline is now one shared row and the diff is computed here, so it
# can be driven at any clock value instead of only in a browser.

SEEN_NOW = datetime(2026, 9, 7, 18, 0, tzinfo=NY)
LONG_AGO = SEEN_NOW - timedelta(hours=8)


def home_payload(**over):
    base = {
        "inbox": {"items": [{"id": "m1", "important": True}]},
        "money": {"today": 20.0, "month": 900.0},
        "portfolio": {"value": 10_000.0},
        "score": {"score": 70},
        "budget": {"over_budget": []},
        "calendar": [],
    }
    base.update(over)
    return base


def test_the_second_device_does_not_re_report_what_the_first_showed():
    """THE ACCEPTANCE SIGNAL: check the hub on your phone, then open it on the
    MacBook — the second must not repeat the first."""
    home = home_payload(inbox={"items": [{"id": "m1", "important": True},
                                         {"id": "m2", "important": True}]})
    old = dash.since_snapshot(home_payload())

    phone = dash.since_changes(old, dash.since_snapshot(home), LONG_AGO, SEEN_NOW)
    assert phone["show"] is True
    assert any("1 new important email" in c["text"] for c in phone["changes"])

    # The phone marked it seen; the MacBook opens a minute later.
    mac = dash.since_changes(dash.since_snapshot(home), dash.since_snapshot(home),
                             SEEN_NOW, SEEN_NOW + timedelta(minutes=1))
    assert mac["show"] is False
    assert mac["reason"] == "too_soon"


def test_a_first_ever_load_reports_nothing_rather_than_everything():
    """With no baseline there is nothing to diff. Inventing an empty one would
    report every email already read as new."""
    out = dash.since_changes(None, dash.since_snapshot(home_payload()), None, SEEN_NOW)
    assert out["show"] is False
    assert out["reason"] == "never_seen"
    assert out["changes"] == []


def test_a_refresh_two_minutes_later_is_too_soon():
    out = dash.since_changes(dash.since_snapshot(home_payload()),
                             dash.since_snapshot(home_payload()),
                             SEEN_NOW, SEEN_NOW + timedelta(minutes=2))
    assert out["show"] is False and out["reason"] == "too_soon"


def test_a_countered_interview_slot_is_surfaced_and_marked_urgent():
    """The pipeline's whole point: they countered and it needs your reply."""
    before = dash.since_snapshot(home_payload(
        calendar=[{"id": "e1", "status": "pending", "title": "Acme"}]))
    after = dash.since_snapshot(home_payload(
        calendar=[{"id": "e1", "status": "countered", "title": "Acme"}]))
    out = dash.since_changes(before, after, LONG_AGO, SEEN_NOW)
    c = [x for x in out["changes"] if x["key"] == "countered"]
    assert c and c[0].get("urgent") is True


def test_a_newly_proposed_slot_is_surfaced():
    before = dash.since_snapshot(home_payload(calendar=[]))
    after = dash.since_snapshot(home_payload(
        calendar=[{"id": "e9", "status": "pending", "title": "Acme"}]))
    out = dash.since_changes(before, after, LONG_AGO, SEEN_NOW)
    assert any(x["key"] == "proposed" for x in out["changes"])


def test_a_hold_that_was_already_countered_is_not_re_announced():
    snap = dash.since_snapshot(home_payload(
        calendar=[{"id": "e1", "status": "countered", "title": "Acme"}]))
    out = dash.since_changes(snap, snap, LONG_AGO, SEEN_NOW)
    assert not [x for x in out["changes"] if x["key"] == "countered"]


def test_crossing_into_over_budget_is_news_but_staying_over_is_not():
    before = dash.since_snapshot(home_payload(budget={"over_budget": ["Dining"]}))
    after = dash.since_snapshot(home_payload(budget={"over_budget": ["Dining", "Gas"]}))
    out = dash.since_changes(before, after, LONG_AGO, SEEN_NOW)
    budget = [x for x in out["changes"] if x["key"] == "budget"]
    assert budget and "Gas" in budget[0]["text"] and "Dining" not in budget[0]["text"]

    same = dash.since_changes(after, after, LONG_AGO, SEEN_NOW)
    assert not [x for x in same["changes"] if x["key"] == "budget"]


def test_new_spending_is_reported_and_a_refund_is_not_called_spending():
    up = dash.since_changes(dash.since_snapshot(home_payload(money={"today": 20.0})),
                            dash.since_snapshot(home_payload(money={"today": 65.0})),
                            LONG_AGO, SEEN_NOW)
    assert any("45" in c["text"] for c in up["changes"] if c["key"] == "spend")
    down = dash.since_changes(dash.since_snapshot(home_payload(money={"today": 65.0})),
                              dash.since_snapshot(home_payload(money={"today": 20.0})),
                              LONG_AGO, SEEN_NOW)
    assert not [c for c in down["changes"] if c["key"] == "spend"]


def test_a_score_that_became_unknown_is_not_a_score_that_fell():
    """null is not a drop to zero — the same rule docs/BUDGETS.md enforces."""
    out = dash.since_changes(dash.since_snapshot(home_payload(score={"score": 70})),
                             dash.since_snapshot(home_payload(score={"score": None})),
                             LONG_AGO, SEEN_NOW)
    assert not [c for c in out["changes"] if c["key"] == "score"]


def test_nothing_changed_produces_an_empty_change_list_not_a_fake_item():
    out = dash.since_changes(dash.since_snapshot(home_payload()),
                             dash.since_snapshot(home_payload()),
                             LONG_AGO, SEEN_NOW)
    assert out["show"] is True and out["changes"] == []


def test_the_snapshot_is_stable_for_an_unchanged_payload():
    """An unstable fingerprint would report a change on every single load."""
    assert dash.since_snapshot(home_payload()) == dash.since_snapshot(home_payload())


def test_the_snapshot_survives_a_malformed_payload():
    for bad in ({}, {"inbox": None}, {"calendar": ["not a dict"]},
                {"inbox": {"items": [{"important": True}]}}):
        assert isinstance(dash.since_snapshot(bad), dict)


def test_an_unreadable_baseline_is_never_seen_not_no_changes():
    for bad in ("a string", [], 0):
        out = dash.since_changes(bad, dash.since_snapshot(home_payload()),
                                 LONG_AGO, SEEN_NOW)
        assert out["show"] is False and out["reason"] == "never_seen"


def test_a_first_load_records_the_baseline_even_though_it_shows_nothing():
    """THE DEADLOCK. `show` and `store` are separate decisions.

    An earlier cut stored the baseline only when the block was successfully
    shown — and nothing can be shown without a baseline, so the first load
    never recorded one, every later load was "never_seen" too, and the feature
    could not start at all. Every unit test still passed, because each one
    handed it a baseline directly; the full phone-then-MacBook walkthrough is
    what exposed it.
    """
    out = dash.since_changes(None, dash.since_snapshot(home_payload()), None, SEEN_NOW)
    assert out["show"] is False
    assert out["store"] is True, "the baseline is never recorded, so it never starts"


def test_a_too_soon_refresh_must_not_overwrite_the_baseline():
    """The other half: an idle tab refreshing in the background would quietly
    consume this morning's changes."""
    out = dash.since_changes(dash.since_snapshot(home_payload()),
                             dash.since_snapshot(home_payload()),
                             SEEN_NOW, SEEN_NOW + timedelta(minutes=2))
    assert out["show"] is False
    assert out["store"] is False, "an idle refresh ate the baseline"


def test_a_shown_block_stores_the_new_baseline():
    out = dash.since_changes(dash.since_snapshot(home_payload()),
                             dash.since_snapshot(home_payload(money={"today": 99.0})),
                             LONG_AGO, SEEN_NOW)
    assert out["show"] is True and out["store"] is True


def test_the_whole_cross_device_sequence():
    """The idea doc's acceptance signal, walked end to end against one shared
    row: first load, phone, MacBook a minute later, a browser that has never
    been opened, and the next morning."""
    seen = {"snapshot": None, "at": None}

    def visit(home, at):
        cur = dash.since_snapshot(home)
        out = dash.since_changes(seen["snapshot"], cur, seen["at"], at)
        if out["store"]:
            seen.update(snapshot=cur, at=at)
        return out

    before = home_payload()
    after = home_payload(inbox={"items": [{"id": "m1", "important": True},
                                          {"id": "m2", "important": True}]})
    t = SEEN_NOW

    assert visit(before, t)["show"] is False               # first ever
    phone = visit(after, t + timedelta(hours=8))
    assert phone["show"] is True and phone["changes"]      # the phone reports
    mac = visit(after, t + timedelta(hours=8, minutes=1))
    assert mac["show"] is False, "the MacBook re-reported what the phone showed"
    fresh = visit(after, t + timedelta(hours=8, minutes=2))
    assert fresh["show"] is False, "a new browser re-reported it"
    # A genuinely new change the next day still lands.
    nextday = visit(home_payload(inbox={"items": [{"id": "m3", "important": True}]}),
                    t + timedelta(days=1))
    assert nextday["show"] is True and nextday["changes"]
