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
# ══ PRODUCT_IDEAS #17 — deadlines were filed and never shown ══════════════
#
# The assistant extracts interview times and bill due dates on every sync and
# writes them to `deadlines`. The only endpoint that exposed them was /space,
# i.e. only the legacy lounge. Bucketing is pure so it can be pinned to a date.

DL_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=NY)


def dl(title, due, source="gmail"):
    return {"title": title, "due_at": due, "source": source}


def test_overdue_is_never_sorted_in_with_upcoming():
    out = dash.deadline_rows([
        dl("Rent", "2026-09-01T00:00:00"),
        dl("Interview", "2026-09-09T14:00:00"),
    ], DL_NOW, NY)
    assert [x["title"] for x in out["overdue"]] == ["Rent"]
    assert [x["title"] for x in out["upcoming"]] == ["Interview"]


def test_a_deadline_with_no_date_is_undated_not_due_now():
    """The extractor stores null when the email carried no date. Rendering
    that as due today would invent a deadline the mail never had."""
    out = dash.deadline_rows([dl("Follow up", None)], DL_NOW, NY)
    assert out["undated"] and out["undated"][0]["title"] == "Follow up"
    assert not out["overdue"] and not out["upcoming"]


def test_upcoming_is_soonest_first_and_overdue_is_most_overdue_first():
    out = dash.deadline_rows([
        dl("Later", "2026-09-20T09:00:00"),
        dl("Sooner", "2026-09-08T09:00:00"),
        dl("Long overdue", "2026-08-01T09:00:00"),
        dl("Just missed", "2026-09-06T09:00:00"),
    ], DL_NOW, NY)
    assert [x["title"] for x in out["upcoming"]] == ["Sooner", "Later"]
    assert [x["title"] for x in out["overdue"]] == ["Just missed", "Long overdue"]


def test_counts_report_the_whole_set_not_just_the_shown_slice():
    rows = [dl(f"D{i}", f"2026-09-{10 + i:02d}T09:00:00") for i in range(10)]
    out = dash.deadline_rows(rows, DL_NOW, NY, limit=3)
    assert len(out["upcoming"]) == 3
    assert out["counts"]["upcoming"] == 10, "the card would understate the backlog"


def test_malformed_rows_are_skipped_rather_than_crashing_the_card():
    out = dash.deadline_rows(
        ["not a dict", {}, {"title": "   "}, dl("Real", "2026-09-09T09:00:00")],
        DL_NOW, NY)
    assert [x["title"] for x in out["upcoming"]] == ["Real"]


def test_an_unparseable_due_date_is_undated_not_silently_dropped():
    out = dash.deadline_rows([dl("Weird", "next tuesday")], DL_NOW, NY)
    assert out["undated"] and out["undated"][0]["title"] == "Weird"


def test_the_boundary_is_now_not_midnight():
    """A deadline earlier today is overdue; one later today is not."""
    out = dash.deadline_rows([
        dl("This morning", "2026-09-07T09:00:00"),
        dl("Tonight", "2026-09-07T20:00:00"),
    ], DL_NOW, NY)
    assert [x["title"] for x in out["overdue"]] == ["This morning"]
    assert [x["title"] for x in out["upcoming"]] == ["Tonight"]


def test_bucketing_holds_across_a_two_year_sweep():
    """Per docs/TESTING.md: never assert against today."""
    from datetime import date as _date
    start = _date(2026, 1, 1)
    for n in range(0, 730, 7):
        d = start + timedelta(days=n)
        now = datetime(d.year, d.month, d.day, 12, tzinfo=NY)
        out = dash.deadline_rows([
            dl("past", (now - timedelta(days=2)).isoformat()),
            dl("future", (now + timedelta(days=2)).isoformat()),
        ], now, NY)
        assert [x["title"] for x in out["overdue"]] == ["past"], d
        assert [x["title"] for x in out["upcoming"]] == ["future"], d


# ══ PRODUCT_IDEAS #17 — settings that were configurable and did nothing ═══
#
# "Alert on move >= (%)" and finance.low_balance both round-tripped through
# Settings and were read by nothing. A setting that silently does nothing is
# worse than a missing one: you configure it, you believe it is on, and you
# stop watching for the thing it was supposed to catch.

def test_a_move_past_the_threshold_produces_an_alert():
    stocks = {"positions": [{"symbol": "AAPL", "change_pct": 4.2},
                            {"symbol": "MSFT", "change_pct": 0.4}],
              "watchlist": []}
    alerts = dash.portfolio_alerts(stocks, 3.0)
    assert [a["symbol"] for a in alerts] == ["AAPL"]
    assert alerts[0]["direction"] == "up"


def test_a_fall_past_the_threshold_alerts_too():
    """"Move" is a magnitude. A 6% drop is the one you most want to know about."""
    alerts = dash.portfolio_alerts(
        {"positions": [{"symbol": "TSLA", "change_pct": -6.0}]}, 3.0)
    assert [a["symbol"] for a in alerts] == ["TSLA"]
    assert alerts[0]["direction"] == "down"


def test_the_threshold_boundary_is_inclusive():
    assert dash.portfolio_alerts(
        {"positions": [{"symbol": "X", "change_pct": 3.0}]}, 3.0)


def test_the_watchlist_is_alerted_on_too():
    alerts = dash.portfolio_alerts(
        {"positions": [], "watchlist": [{"symbol": "NVDA", "change_pct": 9.1}]}, 3.0)
    assert [a["symbol"] for a in alerts] == ["NVDA"]


def test_a_symbol_held_and_watched_is_alerted_once():
    alerts = dash.portfolio_alerts(
        {"positions": [{"symbol": "AAPL", "change_pct": 5.0}],
         "watchlist": [{"symbol": "AAPL", "change_pct": 5.0}]}, 3.0)
    assert len(alerts) == 1


def test_alerts_are_ordered_by_size_of_move():
    alerts = dash.portfolio_alerts({"positions": [
        {"symbol": "A", "change_pct": 3.5},
        {"symbol": "B", "change_pct": -9.9},
        {"symbol": "C", "change_pct": 6.0}]}, 3.0)
    assert [a["symbol"] for a in alerts] == ["B", "C", "A"]


def test_a_position_with_no_quote_is_not_a_move():
    """available: False means no data, which is not a 0% day."""
    assert dash.portfolio_alerts(
        {"positions": [{"symbol": "AAPL", "available": False}]}, 3.0) == []


@pytest.mark.parametrize("threshold", [None, 0, "", "abc"])
def test_an_unset_threshold_alerts_on_nothing(threshold):
    """An alert the user did not ask for is its own lie about the setting."""
    assert dash.portfolio_alerts(
        {"positions": [{"symbol": "AAPL", "change_pct": 50.0}]}, threshold) == []


def test_low_balance_flags_accounts_at_or_under_the_floor():
    out = dash.low_balance_accounts(
        [{"name": "Checking", "balance": 40.0},
         {"name": "Savings", "balance": 900.0},
         {"name": "Spare", "balance": 100.0}], 100)
    assert [a["name"] for a in out] == ["Checking", "Spare"]


def test_low_balance_never_treats_a_missing_balance_as_zero():
    """An account whose balance could not be read is not an empty account."""
    out = dash.low_balance_accounts(
        [{"name": "Unknown", "balance": None}, {"name": "Broken"}], 100)
    assert out == []


def test_low_balance_reports_the_worst_first():
    out = dash.low_balance_accounts(
        [{"name": "A", "balance": 90}, {"name": "B", "balance": -20}], 100)
    assert [a["name"] for a in out] == ["B", "A"]


@pytest.mark.parametrize("floor", [None, "abc"])
def test_an_unusable_low_balance_floor_flags_nothing(floor):
    assert dash.low_balance_accounts([{"name": "A", "balance": 0}], floor) == []
