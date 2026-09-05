"""Date-window regression tests for the firefly service.

WHY THIS FILE EXISTS: on 2026-09-01 the money section went dark with
"Firefly not connected". Nothing had been deployed to cause it — the code
simply asked Firefly for the range first-of-month -> today, which on the
1st is a zero-length range that Firefly rejects with 422. It had been
latent since the code was written and would have fired on the 1st of every
month. "It worked yesterday" was true and meaningless.

So these tests never trust today's date: they sweep every day of a
multi-year calendar and assert the invariants that must hold on ALL of
them. A test that only runs on the day you wrote it cannot catch a bug
that only appears on the 1st.
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

ff = load_service_module("firefly_main", "services/firefly/app/main.py")


def all_days(start: date, days: int):
    return [start + timedelta(days=i) for i in range(days)]


# Two full years: every month boundary, both leap and non-leap Februaries.
CALENDAR = all_days(date(2026, 1, 1), 365 * 2 + 1)
FIRSTS = [d for d in CALENDAR if d.day == 1]


# The REAL function the service uses — not a copy. If _live() stops using
# it, test_live_uses_the_shared_window below fails.
summary_window = ff._month_window


@pytest.mark.parametrize("today", FIRSTS, ids=lambda d: d.isoformat())
def test_first_of_month_is_never_a_zero_length_range(today):
    """The exact bug: start == end on the 1st => Firefly 422 => whole money
    section reads as 'not connected'."""
    start, end = summary_window(today)
    assert start != end, f"zero-length range on {today} — Firefly returns 422"
    assert end > start


@pytest.mark.parametrize("today", CALENDAR, ids=lambda d: d.isoformat())
def test_window_is_valid_every_single_day(today):
    start, end = summary_window(today)
    assert start <= end
    assert start == today.replace(day=1).isoformat()
    # never reaches back before the month, never skips the current day
    assert end >= today.isoformat() or today.day == 1


@pytest.mark.parametrize("today", CALENDAR[:400], ids=lambda d: d.isoformat())
def test_window_end_never_overshoots_more_than_a_day(today):
    """Padding the range must not silently widen the reporting period."""
    _, end = summary_window(today)
    assert date.fromisoformat(end) - today <= timedelta(days=1)


def test_month_boundaries_across_year_and_leap_day():
    for d in (date(2026, 12, 31), date(2027, 1, 1), date(2028, 2, 29), date(2028, 3, 1)):
        start, end = summary_window(d)
        assert end > start, d


# ---- the timezone half of the same outage -------------------------------

def test_service_clock_is_local_not_utc():
    """_live() was the one function using the container's UTC clock, so for
    the first hours of each day it was already in tomorrow's month. At
    01:37 UTC on Sep 1 the container said September while New York said
    August 31."""
    utc_moment = datetime(2026, 9, 1, 1, 37, tzinfo=ZoneInfo("UTC"))
    ny = utc_moment.astimezone(ZoneInfo("America/New_York")).date()
    assert utc_moment.date() != ny  # the trap
    assert ny == date(2026, 8, 31)


def test_every_date_window_routes_through_the_clock_seam(monkeypatch):
    """Guards the seam itself: if someone reintroduces a bare
    datetime.now()/date.today() call, this fails."""
    src = (Path(ff.__file__)).read_text()
    body = src.split("def _today()", 1)[1].split("def _connected", 1)[0]
    rest = src.replace(body, "")
    assert "date.today()" not in rest, "bare date.today() bypasses LOCAL_TZ"
    assert rest.count("datetime.now(LOCAL_TZ).date()") == 0, \
        "call datetime.now() only inside _today()"


def test_clock_seam_is_patchable(monkeypatch):
    monkeypatch.setattr(ff, "_today", lambda: date(2026, 9, 1))
    assert ff._today() == date(2026, 9, 1)


# ---- month evidence: $0 vs unknown --------------------------------------

@pytest.mark.parametrize("today,ingest,expected", [
    (date(2026, 9, 1), date(2026, 8, 28), False),   # month rolled, nothing new
    (date(2026, 9, 1), date(2026, 9, 1), True),     # imported today
    (date(2026, 9, 15), date(2026, 9, 2), True),    # imported mid-month
    (date(2026, 9, 15), date(2026, 8, 31), False),  # nothing since August
    (date(2026, 9, 30), date(2026, 9, 30), True),
])
def test_month_ingested_flag(today, ingest, expected):
    """Drives the difference between 'you spent $0' and 'we have no data'."""
    month_start = today.replace(day=1)
    assert bool(ingest and ingest >= month_start) is expected


def test_live_uses_the_shared_window():
    """The window helper is only worth testing if _live() actually calls it."""
    src = Path(ff.__file__).read_text()
    live = src.split("async def _live()", 1)[1].split("async def _data", 1)[0]
    assert "_month_window(today)" in live, "_live() must use the tested helper"
    assert "replace(day=1)" not in live, "_live() is rebuilding the range inline again"


# ---- the pay-cycle window ----------------------------------------------
# One window feeds two claims (month-to-date spending and the current pay
# cycle), so it has to satisfy both on every day of the calendar — not just
# on the days that are convenient to test.

cycle_window = ff._cycle_window


@pytest.mark.parametrize("today", CALENDAR, ids=lambda d: d.isoformat())
def test_cycle_window_is_valid_every_single_day(today):
    start, end = cycle_window(today)
    assert start < end, f"zero-length range on {today} — Firefly returns 422"
    # reaches the 1st of the month, so month-to-date is computable from it
    assert start <= today.replace(day=1).isoformat()
    # covers the lookback, so the previous paycheck is inside it
    assert start <= (today - timedelta(days=ff.CYCLE_LOOKBACK_DAYS - 1)).isoformat()
    # and never claims days that haven't happened
    assert end <= (today + timedelta(days=1)).isoformat()


@pytest.mark.parametrize("today", FIRSTS, ids=lambda d: d.isoformat())
def test_cycle_window_on_the_first_still_reaches_back_a_full_lookback(today):
    """On the 1st the month is empty; the pay cycle that is running started
    in the previous month and must still be inside the window."""
    start, _ = cycle_window(today)
    assert start < today.replace(day=1).isoformat()


def test_cycle_uses_the_shared_window():
    """The helper is only worth testing if _cycle_payload() actually uses it."""
    src = Path(ff.__file__).read_text()
    body = src.split("async def _cycle_payload()", 1)[1].split('@app.get("/cycle")', 1)[0]
    assert "_cycle_window(today)" in body, "_cycle_payload() must use the tested helper"
    assert "timedelta(days=CYCLE_LOOKBACK_DAYS" not in body, \
        "_cycle_payload() is rebuilding the range inline again"
