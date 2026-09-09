"""The parser that books interviews onto the real Google Calendar.

WHY THIS FILE EXISTS: `extract_datetime` turns "Interview scheduled: Fri Jul
25, 2:00 PM" into a timestamp. `main` posts that to the schedule service,
which pushes it to Google Calendar. It is date logic with a side effect on an
external system, and it had neither a timezone, a year rollover, nor a test
(SCRUM-134).

It returned `datetime(utcnow().year, month, day, ...)` — naive, always the
current year, and reading that year off UTC. So in December an email saying
"Jan 8, 2:00 PM" booked January 8th of the year that was ENDING: eleven months
in the past, landing behind you in the calendar, invisible to
`_event_minutes_until` because that only looks forward. You find out by not
being at the interview.

Same class as SCRUM-64, one file over: that one miscounted a workout, this one
misses an interview.

Per docs/TESTING.md, the date behaviour is swept across a two-year calendar
through the `now` seam rather than tested against today.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from conftest import load_service_module  # noqa: E402

orch = load_service_module("assistant_orchestrator",
                           "services/assistant/app/orchestrator.py")

EASTERN = ZoneInfo("America/New_York")


def at(y, m, d, hh=9, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=EASTERN)


def parsed(text, now):
    iso = orch.extract_datetime(text, now=now)
    return datetime.fromisoformat(iso) if iso else None


# --- defect 1: no timezone --------------------------------------------------

def test_the_result_carries_an_offset():
    """A naive timestamp handed to a calendar is the reliable way to put an
    event hours from where it belongs."""
    dt = parsed("Interview scheduled: Fri Jul 25, 2:00 PM", at(2026, 7, 1))
    assert dt.tzinfo is not None
    assert dt.utcoffset() is not None


def test_the_offset_is_the_real_one_for_that_date():
    """Not a fixed offset: July is EDT (-4), January is EST (-5). A constant
    would be wrong for half the year."""
    summer = parsed("Jul 25, 2:00 PM", at(2026, 7, 1))
    winter = parsed("Jan 8, 2:00 PM", at(2026, 1, 2))
    assert summer.utcoffset() == timedelta(hours=-4)
    assert winter.utcoffset() == timedelta(hours=-5)


def test_two_pm_stays_two_pm_locally():
    """The hour in the email is a local wall-clock hour and must survive."""
    dt = parsed("Interview: Jul 25, 2:00 PM", at(2026, 7, 1))
    assert (dt.hour, dt.minute) == (14, 0)


# --- defect 2: no year rollover ---------------------------------------------

def test_the_acceptance_signal():
    """The ticket's own: parsing "Jan 8, 2:00 PM" on December 20th returns
    NEXT January, tz-aware."""
    dt = parsed("Interview scheduled: Jan 8, 2:00 PM", at(2026, 12, 20))
    assert (dt.year, dt.month, dt.day) == (2027, 1, 8)
    assert dt.tzinfo is not None


@pytest.mark.parametrize("day", range(1, 32))
def test_every_december_day_rolls_january_forward(day):
    dt = parsed("Interview: Jan 8, 2:00 PM", at(2026, 12, day))
    assert dt.year == 2027, day
    assert dt > at(2026, 12, day), "a booked interview is never in the past"


def test_a_date_later_this_year_does_not_roll():
    dt = parsed("Interview: Dec 25, 10:00 AM", at(2026, 12, 20))
    assert (dt.year, dt.month, dt.day) == (2026, 12, 25)


def test_todays_date_is_today_not_next_year():
    """Read at 4pm, an email naming today at 2pm means today. Rolling it a
    year forward because the hour has passed would be a worse bug than the
    one being fixed."""
    dt = parsed("Interview: Mar 3, 2:00 PM", at(2026, 3, 3, 16, 0))
    assert (dt.year, dt.month, dt.day) == (2026, 3, 3)


# --- the two-year sweep -----------------------------------------------------

def _every_day(start, days):
    return [start + timedelta(days=i) for i in range(days)]


SWEEP = _every_day(at(2026, 1, 1), 730)


@pytest.mark.parametrize("today", SWEEP, ids=lambda d: d.date().isoformat())
def test_a_booked_interview_is_never_in_the_past(today):
    """The invariant that matters, asserted on all 730 days: whatever date the
    email names, the event that gets created is ahead of the day it was read.
    An interview booked into the past is one you do not attend."""
    for text in ("Interview scheduled: Jan 8, 2:00 PM",
                 "Interview: Mar 3, 10:00 AM",
                 "Interview: Jul 25, 2:00 PM",
                 "Phone screen Nov 12, 4:30 PM"):
        dt = parsed(text, today)
        assert dt is not None, (text, today)
        assert dt.date() >= today.date(), (text, today, dt)


@pytest.mark.parametrize("today", SWEEP[::37], ids=lambda d: d.date().isoformat())
def test_the_sweep_never_lands_more_than_a_year_out(today):
    """The mirror of the rollover: pushing every date forward would be just as
    wrong in the other direction."""
    dt = parsed("Interview: Jul 25, 2:00 PM", today)
    assert dt.date() <= (today + timedelta(days=366)).date(), (today, dt)


@pytest.mark.parametrize("today", SWEEP[::11], ids=lambda d: d.date().isoformat())
def test_every_result_is_aware_on_every_day(today):
    assert parsed("Interview: Jul 25, 2:00 PM", today).tzinfo is not None


# --- defect 3: the year came off UTC ----------------------------------------

def test_new_years_eve_uses_the_local_year_not_utc():
    """At 21:00 on Dec 31 in New York it is already Jan 1 in UTC. The old code
    read the year off utcnow(), so in the one window where the rollover
    matters most it was reading the wrong year to roll from."""
    nye = datetime(2026, 12, 31, 21, 0, tzinfo=EASTERN)
    assert nye.astimezone(ZoneInfo("UTC")).year == 2027, "premise of this test"
    dt = parsed("Interview: Jan 8, 2:00 PM", nye)
    assert (dt.year, dt.month, dt.day) == (2027, 1, 8)


def test_the_seam_accepts_a_naive_now_without_exploding():
    """main calls this with no `now` at all; a caller passing a naive one
    should not crash the sync cycle."""
    assert orch.extract_datetime("Interview: Jul 25, 2:00 PM",
                                 now=datetime(2026, 7, 1, 9, 0)) is not None


# --- parsing itself ---------------------------------------------------------

def test_a_word_ending_in_a_month_name_is_not_a_date():
    """Without a leading word boundary "Trojan 25" contains "jan 25", and an
    interview gets booked in January off a sponsor's name."""
    assert orch.extract_datetime("Trojan 25 update", now=at(2026, 6, 1)) is None


def test_no_date_is_none_not_a_guess():
    assert orch.extract_datetime("Let's chat sometime", now=at(2026, 6, 1)) is None


@pytest.mark.parametrize("text,expected_hour", [
    ("Interview Jul 25, 2:00 PM", 14),
    ("Interview Jul 25, 2 PM", 14),
    ("Interview Jul 25, 2:00 p.m.", 14),
    ("Interview Jul 25, 11:30 AM", 11),
    ("Interview Jul 25, 12:00 PM", 12),   # noon is 12, not 24
    ("Interview Jul 25, 12:00 AM", 0),    # midnight is 0, not 12
])
def test_times_parse(text, expected_hour):
    assert parsed(text, at(2026, 7, 1)).hour == expected_hour


def test_a_bare_date_defaults_to_9am():
    assert parsed("Interview on Jul 25", at(2026, 7, 1)).hour == 9


def test_an_impossible_date_is_none():
    assert orch.extract_datetime("Interview Feb 30, 2:00 PM", now=at(2026, 1, 1)) is None


def test_feb_29_rolling_into_a_non_leap_year_is_none_not_a_crash():
    """Feb 29 2028 exists; 2029 does not. The rollover must not raise inside
    the sync cycle."""
    assert orch.extract_datetime("Interview Feb 29, 2:00 PM",
                                 now=at(2028, 6, 1)) is None


# --- agreement with the other parser in this repo ---------------------------

def _dateparse():
    return load_service_module("gmail_dateparse", "services/gmail/app/dateparse.py")


@pytest.mark.parametrize("today", SWEEP[::29], ids=lambda d: d.date().isoformat())
def test_it_agrees_with_dateparse_on_the_rollover_rule(today):
    """gmail/dateparse.py already resolved the year correctly. The two parsers
    cannot be merged while each service builds from its own directory, but a
    date must not mean two different things depending on which one read the
    mail — so the rule is pinned against the other implementation, on 25 days
    spread across the sweep.

    Compared as a DATE and an offset, not as an instant: `extract_slots`
    segments on commas so it can read "Mon 10am or Tue 2pm", which means
    "Jan 8, 2:00 PM" yields both a bare-date 9am slot and the 2pm one. That
    is its job, not a disagreement about what "Jan 8" means.
    """
    mine = parsed("Jan 8, 2:00 PM", today)
    theirs = [datetime.fromisoformat(s)
              for s in _dateparse().extract_slots("Jan 8, 2:00 PM", now=today)]
    assert {t.date() for t in theirs} == {mine.date()}, (today, mine, theirs)
    assert mine in theirs, (today, mine, theirs)


def test_both_parsers_read_a_dotted_meridiem():
    """`\b` cannot follow a pattern ending in "." — so `am|pm|a\.m\.|p\.m\.`
    never matched the dotted forms in EITHER file. "2:00 p.m." did not fail
    loudly; it fell through to the 9am default and put a wrong time on a real
    calendar. Found by diffing the two parsers for this ticket."""
    mine = parsed("Interview Jul 25, 2:00 p.m.", at(2026, 7, 1))
    assert mine.hour == 14

    slots = _dateparse().extract_slots("I'm available Jul 25, 2:00 p.m.",
                                       now=at(2026, 7, 1))
    assert any(datetime.fromisoformat(s).hour == 14 for s in slots), slots


def test_a_meridiem_glued_to_a_word_is_still_rejected():
    """The boundary was doing something, even if not what it looked like."""
    assert parsed("Interview Jul 25, 2:00 pmx", at(2026, 7, 1)).hour == 9
