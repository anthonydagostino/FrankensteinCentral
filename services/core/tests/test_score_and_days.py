"""Two things the dashboard states that it did not know.

Both come from `docs/PRODUCT_IDEAS.md` group A, and both are the same defect
`docs/BUDGETS.md` already forbids one service over: **zero and unknown are
different states**. The money layer got that rule; the score and the habit
counters did not.

Per `docs/TESTING.md`, nothing here asserts against today's date — the day
bucketing is swept across two years, because a bug that only bites after 8pm
local passes every test run in the morning.
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

# The pure seam module, so these run without a database — the reason main.py
# had no tests despite owning the numbers on the header.
core = load_service_module("core_daymath", "services/core/app/daymath.py")

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

WEIGHTS = {"tasks": 20, "fitness": 20, "study": 20, "water": 20, "nutrition": 20}


# ══ #16 — an unset goal is not a failed goal ══════════════════════════════

def test_a_day_with_nothing_logged_has_no_score_rather_than_a_bad_one():
    """THE REGRESSION. A fresh morning used to read 0, which is a statement
    about your day that the app has no evidence for."""
    out = core.compute_score({k: None for k in WEIGHTS}, WEIGHTS)
    assert out["score"] is None, "an untracked day was scored instead of reported"
    assert out["tracked"] == 0
    assert out["of"] == 5


def test_an_unset_component_does_not_cost_its_weight():
    """Three of five tracked and all complete is 100 of what was tracked."""
    out = core.compute_score(
        {"tasks": None, "nutrition": None, "fitness": 1.0, "study": 1.0, "water": 1.0},
        WEIGHTS)
    assert out["score"] == 100, "unset components were scored as misses"
    assert out["tracked"] == 3
    assert out["of"] == 5


def test_unset_and_failed_are_different_numbers():
    """The heart of it: these two days were indistinguishable."""
    never_set = core.compute_score(
        {"tasks": None, "fitness": 1.0, "study": 1.0, "water": 1.0, "nutrition": None},
        WEIGHTS)
    set_and_missed = core.compute_score(
        {"tasks": 0.0, "fitness": 1.0, "study": 1.0, "water": 1.0, "nutrition": None},
        WEIGHTS)
    assert never_set["score"] != set_and_missed["score"], \
        "setting a goal and missing it scores the same as never setting one"
    assert never_set["score"] == 100
    assert set_and_missed["score"] == 75


def test_a_zero_is_still_a_zero():
    """Excluding `None` must not accidentally excuse a real miss."""
    out = core.compute_score({k: 0.0 for k in WEIGHTS}, WEIGHTS)
    assert out["score"] == 0
    assert out["tracked"] == 5


def test_parts_say_which_components_were_tracked():
    out = core.compute_score({"tasks": None, "fitness": 0.5}, {"tasks": 20, "fitness": 20})
    assert out["parts"]["tasks"] == {"ratio": None, "weight": 20, "tracked": False}
    assert out["parts"]["fitness"]["tracked"] is True


def test_a_disabled_component_is_still_excluded():
    """Pre-existing behaviour that must survive: weight 0 drops out."""
    out = core.compute_score({"tasks": 1.0, "fitness": 0.0},
                             {"tasks": 20, "fitness": 0})
    assert out["score"] == 100
    assert "fitness" not in out["parts"]
    assert out["of"] == 1


def test_ratios_are_still_clamped():
    out = core.compute_score({"tasks": 5.0, "fitness": -3.0},
                             {"tasks": 20, "fitness": 20})
    assert out["parts"]["tasks"]["ratio"] == 1.0
    assert out["parts"]["fitness"]["ratio"] == 0.0
    assert out["score"] == 50


# ══ #15 — a workout counts on the day you did it ══════════════════════════

def stored_by_fitness_before_the_fix(local_dt):
    """What the old `datetime.utcnow().isoformat()` wrote: naive UTC."""
    return local_dt.astimezone(UTC).replace(tzinfo=None).isoformat()


def stored_by_fitness_now(local_dt):
    """What `_visit_instant` writes: the instant, with its offset."""
    return local_dt.isoformat()


@pytest.mark.parametrize("hour", [0, 7, 12, 19, 20, 21, 23])
def test_a_visit_is_credited_to_the_local_day_it_happened(hour):
    """A 9pm workout is today's workout, not tomorrow's."""
    local = datetime(2026, 9, 6, hour, 30, tzinfo=NY)
    for stored in (stored_by_fitness_now(local),
                   stored_by_fitness_before_the_fix(local)):
        assert core.local_day(stored) == local.date(), \
            f"{stored} bucketed to {core.local_day(stored)}, not {local.date()}"


def test_a_sunday_evening_workout_counts_for_that_week():
    """THE REGRESSION the idea doc named: it landed in next week's bucket, so
    the dashboard could tell you to go to the gym you just came back from."""
    local = datetime(2026, 9, 6, 21, 30, tzinfo=NY)   # a Sunday
    assert local.weekday() == 6, "fixture is not a Sunday"
    day = core.local_day(stored_by_fitness_before_the_fix(local))
    assert core.week_start(day) == core.week_start(local.date()), \
        "a Sunday-evening workout was credited to the following week"


def sweep():
    """Every local day across two years, at the hour the bug lived in."""
    start = date(2026, 1, 1)
    for n in range(730):
        yield start + timedelta(days=n)


def test_every_evening_of_two_years_buckets_to_its_own_local_day():
    """The bug was invisible in the morning and certain at night, so the sweep
    walks the evening. Covers both DST transitions and a leap day."""
    wrong = []
    for d in sweep():
        local = datetime(d.year, d.month, d.day, 21, 30, tzinfo=NY)
        for stored in (stored_by_fitness_now(local),
                       stored_by_fitness_before_the_fix(local)):
            if core.local_day(stored) != d:
                wrong.append((d, stored))
    assert not wrong, f"{len(wrong)} evenings bucketed to the wrong day, e.g. {wrong[:3]}"


def test_every_day_of_two_years_buckets_correctly_across_the_whole_clock():
    wrong = []
    for d in sweep():
        for hour in (0, 6, 13, 20, 23):
            local = datetime(d.year, d.month, d.day, hour, tzinfo=NY)
            if core.local_day(stored_by_fitness_now(local)) != d:
                wrong.append((d, hour))
    assert not wrong, f"{len(wrong)} (day, hour) pairs bucketed wrong, e.g. {wrong[:3]}"


def test_dst_transition_days_are_not_special_cased_wrong():
    """2026: DST starts 8 March, ends 1 November in America/New_York."""
    for d in (date(2026, 3, 8), date(2026, 11, 1)):
        for hour in (1, 3, 21, 23):
            local = datetime(d.year, d.month, d.day, hour, tzinfo=NY)
            assert core.local_day(stored_by_fitness_now(local)) == d, (d, hour)


def test_a_bare_date_string_is_never_shifted():
    """A date-only value has no instant, so there is nothing to convert. Reading
    naive midnight as UTC would walk an exam date backwards a day in New York --
    `local_day` is also what `_exam()` parses its target date with."""
    for d in (date(2026, 9, 6), date(2026, 1, 1), date(2026, 12, 31)):
        assert core.local_day(d.isoformat()) == d
    assert core.local_day("2026-09-06T00:00:00-04:00") == date(2026, 9, 6)


def test_unparseable_and_empty_are_none_not_a_guess():
    assert core.local_day("") is None
    assert core.local_day("not a date") is None
    assert core.local_day(None) is None
