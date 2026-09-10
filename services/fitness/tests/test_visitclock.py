"""A workout is logged at the moment it happened, unambiguously.

WHY THIS FILE EXISTS: `docs/PRODUCT_IDEAS.md` #15. This service stored visits as
`datetime.utcnow().isoformat()` — naive UTC — while importing `EASTERN` with a
comment saying "today for a workout plan has to mean the user's actual day".
The awareness was here; the code was not. A 9pm New York workout is 01:00 UTC
tomorrow, so `core` credited it to the wrong day, and a Sunday-evening one to
the wrong WEEK. It fed the score component with the second-heaviest weight.

Per `docs/TESTING.md`, nothing here is asserted against today's date.
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

vc = load_service_module("fitness_visitclock", "services/fitness/app/visitclock.py")

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def local_day_of(stored):
    """How `core.daymath.local_day` reads it back — the contract that matters."""
    core = load_service_module("core_daymath", "services/core/app/daymath.py")
    return core.local_day(stored)


def test_a_logged_visit_carries_an_offset():
    """The whole defect in one assertion: a naive string forces every reader to
    guess, and the reader guessed wrong."""
    out = vc.visit_instant(None, now=datetime(2026, 9, 6, 21, 30, tzinfo=NY))
    assert datetime.fromisoformat(out).tzinfo is not None, \
        f"stored without an offset: {out}"


@pytest.mark.parametrize("hour", [0, 7, 12, 19, 20, 21, 23])
def test_a_visit_reads_back_on_the_day_it_was_logged(hour):
    local = datetime(2026, 9, 6, hour, 30, tzinfo=NY)
    assert local_day_of(vc.visit_instant(None, now=local)) == local.date()


def test_a_sunday_evening_visit_stays_in_its_own_week():
    local = datetime(2026, 9, 6, 21, 30, tzinfo=NY)
    assert local.weekday() == 6
    core = load_service_module("core_daymath", "services/core/app/daymath.py")
    stored = vc.visit_instant(None, now=local)
    assert core.week_start(core.local_day(stored)) == core.week_start(local.date())


def test_a_caller_supplied_time_without_an_offset_is_local_not_utc():
    """Someone typing "8pm" means 8pm where they are. Reading it as UTC would
    file an evening workout as an afternoon one on the wrong day."""
    out = vc.visit_instant("2026-09-06T20:00:00")
    assert datetime.fromisoformat(out).utcoffset() is not None
    assert local_day_of(out) == date(2026, 9, 6)


def test_a_caller_supplied_offset_is_respected():
    out = vc.visit_instant("2026-09-06T20:00:00+09:00")
    assert datetime.fromisoformat(out).utcoffset().total_seconds() == 9 * 3600


def test_an_unparseable_value_is_kept_rather_than_replaced_with_now():
    """A wrong value the user can see beats a plausible one they cannot."""
    assert vc.visit_instant("last tuesday") == "last tuesday"


def test_every_evening_of_two_years_round_trips_to_its_own_day():
    """The sweep `docs/TESTING.md` requires: the bug was invisible before 8pm
    local and certain after it, so a single-date test proves nothing."""
    core = load_service_module("core_daymath", "services/core/app/daymath.py")
    start = date(2026, 1, 1)
    wrong = []
    for n in range(730):
        d = start + timedelta(days=n)
        for hour in (20, 21, 22, 23):
            local = datetime(d.year, d.month, d.day, hour, tzinfo=NY)
            if core.local_day(vc.visit_instant(None, now=local)) != d:
                wrong.append((d, hour))
    assert not wrong, f"{len(wrong)} evening visits round-tripped wrong, e.g. {wrong[:3]}"
