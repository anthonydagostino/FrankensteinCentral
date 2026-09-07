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


# ---- the month headline says which of its two sources it is quoting ------
#
# Codex's finding against the first correction: the paycheck panel learned to
# say "at least", and the headline above it went on printing an exact
# month-to-date total from a window that had been truncated. They are fed by
# different services, so a caveat on one is not a caveat on the other — and
# the case that most needs it, a truncated window with NO matching paycheck,
# is exactly the one that takes the other branch.

PAY_FOUND = {"configured": True, "available": True,
             "month": {"spent": 312.0, "spent_is_lower_bound": False}}
PAY_FOUND_PARTIAL = {"configured": True, "available": True,
                     "month": {"spent": 312.0, "spent_is_lower_bound": True}}
# A configured paycheck the ledger could not find: `available` is False and
# the brief drops everything, which is how the caveat used to get lost.
PAY_MISSING = {"configured": True, "available": False, "month": None}

SPENDING_WHOLE = {"connected": True, "month": 480.0, "month_ingested": True,
                  "window_complete": True}
SPENDING_PARTIAL = {**SPENDING_WHOLE, "window_complete": False}
SPENDING_SILENT = {"connected": True, "month": 480.0, "month_ingested": True}


def test_a_complete_month_is_quoted_as_a_total():
    assert dash.month_spend_claim(SPENDING_WHOLE, PAY_FOUND) == (312.0, False)


def test_a_truncated_pay_cycle_month_is_a_lower_bound():
    assert dash.month_spend_claim(SPENDING_WHOLE, PAY_FOUND_PARTIAL) == (312.0, True)


def test_a_truncated_month_with_no_matching_paycheck_is_still_a_lower_bound():
    """The case the first fix missed entirely.

    No paycheck was found, so the pay-cycle figure is unavailable and the
    headline falls back to /spending. That window was truncated too, and the
    caveat has to survive the fallback.
    """
    value, lower = dash.month_spend_claim(SPENDING_PARTIAL, PAY_MISSING)
    assert value == 480.0
    assert lower is True


def test_no_paycheck_configured_and_a_truncated_month_is_a_lower_bound():
    value, lower = dash.month_spend_claim(SPENDING_PARTIAL, {"configured": False})
    assert value == 480.0
    assert lower is True


def test_a_spending_payload_that_does_not_say_is_treated_as_unknown():
    """Same rule as the engine: silence is not proof of a whole window."""
    assert dash.month_spend_claim(SPENDING_SILENT, PAY_MISSING) == (480.0, True)


def test_a_whole_month_without_a_paycheck_is_still_an_exact_total():
    """The guard against over-suppression — the ordinary no-paycheck setup
    must not start hedging every figure."""
    assert dash.month_spend_claim(SPENDING_WHOLE, PAY_MISSING) == (480.0, False)


def test_an_unimported_month_is_unknown_not_a_lower_bound_of_zero():
    """`month_ingested: False` means nothing is in the ledger yet. "At least
    $0" would be a claim; None is the honest answer."""
    assert dash.month_spend_claim(
        {**SPENDING_WHOLE, "month_ingested": False}, PAY_MISSING) == (None, False)


def test_unreachable_spending_claims_nothing():
    assert dash.month_spend_claim({}, {}) == (None, False)


def test_the_home_renderer_actually_consumes_the_lower_bound_flag():
    """Pins the payload field to the surface that has to show it.

    The whole finding was a flag that existed and was never rendered, so a
    test that only checks the payload would have passed against the bug.
    """
    from pathlib import Path
    home = Path(__file__).resolve().parents[3] / "gateway" / "static" / "home.js"
    src = home.read_text()
    assert "month_is_lower_bound" in src
    # And on the headline, not merely somewhere in the file.
    headline = src[src.index("Spent in ") - 800:src.index("Spent in ") + 400]
    assert "month_is_lower_bound" in headline


def test_a_lower_bound_is_never_claimed_on_a_number_we_do_not_have():
    """Found reviewing my own diff, not by a reviewer.

    A truncated window with no month figure returned `(None, True)`, which the
    card renders as "—" captioned "at least — part of the ledger couldn't be
    read". That reads as though something is known when nothing is. A lower
    bound is a claim ABOUT a number; with no number there is nothing to bound.
    """
    assert dash.month_spend_claim(
        {"connected": True, "month": None, "month_ingested": True,
         "window_complete": False}, {}) == (None, False)


def test_a_genuine_zero_month_can_still_be_a_lower_bound():
    """The guard against fixing that too broadly. $0 spent so far IS a number,
    and a truncated window means it might really be more."""
    assert dash.month_spend_claim(
        {"connected": True, "month": 0.0, "month_ingested": True,
         "window_complete": False}, {}) == (0.0, True)
