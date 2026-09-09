"""Being able to say "handled" (PRODUCT_IDEAS #34).

Nothing in this product could be told "not now" or "not ever". An email you
had consciously decided not to answer stayed at the top of the card for seven
days; Do-Next recomputed on every load with no memory and re-suggested what
you had just done. An attention system that cannot be told "handled" trains
you to stop reading it.

The rule these tests exist to hold is the direction of failure. Showing you
something you had hidden is a nuisance; hiding something because a timestamp
could not be parsed, or because the service that stores dismissals was down,
is data loss you cannot see. Every ambiguous case here resolves to SHOWN.

Per docs/TESTING.md the expiry is swept across a calendar rather than checked
against today: "not today" is exactly the kind of rule that works every day of
the year except the two the clock changes.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

core = load_service_module("core_daymath_dismiss", "services/core/app/daymath.py")

NY = ZoneInfo("America/New_York")


def row(key, expires_at, **kw):
    return {"key": key, "expires_at": expires_at, **kw}


# --- expiry ------------------------------------------------------------------

def test_forever_never_expires():
    assert core.dismissal_expiry("forever", datetime(2026, 5, 1, tzinfo=NY)) is None


def test_today_expires_at_the_next_local_midnight():
    now = datetime(2026, 5, 1, 14, 30, tzinfo=NY)
    exp = core.dismissal_expiry("today", now)
    assert exp.year, exp.month == (2026, 5)
    assert (exp.day, exp.hour, exp.minute) == (2, 0, 0)
    assert exp.tzinfo is not None


def test_today_at_one_minute_to_midnight_expires_in_one_minute():
    """Literal on purpose. "Not today" means not today, and rounding it up to
    a full day would be inventing an intent the click did not express."""
    now = datetime(2026, 5, 1, 23, 59, tzinfo=NY)
    exp = core.dismissal_expiry("today", now)
    assert (exp - now).total_seconds() == 60


def test_today_expiry_is_swept_across_a_calendar_including_dst():
    """800 consecutive days, four times of day. The expiry must always be the
    next local midnight and always be in the future — an expiry built by
    adding 86400s lands an hour off on the days the clock changes, which turns
    "not today" into "not until 1am tomorrow" or expires it an hour early.
    """
    start = datetime(2026, 1, 1, tzinfo=NY)
    for day in range(800):
        for hour in (0, 6, 13, 23):
            now = (start + timedelta(days=day)).replace(hour=hour)
            exp = core.dismissal_expiry("today", now)
            assert exp > now, f"{now} -> {exp} is not in the future"
            assert (exp.hour, exp.minute, exp.second) == (0, 0, 0), (
                f"{now.isoformat()} -> {exp.isoformat()} is not a local midnight")
            # It is the NEXT midnight, never a later one.
            assert (exp.date() - now.date()).days == 1, (
                f"{now.isoformat()} -> {exp.isoformat()} skipped a day")


@pytest.mark.parametrize("anchor", ["2026-03-08", "2026-11-01", "2027-03-14", "2027-11-07"])
def test_a_today_snooze_never_outlives_the_day_across_a_dst_boundary(anchor):
    """On the 23-hour day a naive +86400 expiry runs an hour into tomorrow, so
    the thing you hid for today is still hidden tomorrow morning."""
    d = datetime.fromisoformat(anchor).replace(tzinfo=NY)
    for hour in range(24):
        now = d.replace(hour=hour)
        exp = core.dismissal_expiry("today", now)
        tomorrow_9am = (now + timedelta(days=1)).replace(hour=9, minute=0)
        assert not core.dismissal_active(exp, tomorrow_9am), (
            f"snoozed at {now.isoformat()}, still hidden at {tomorrow_9am.isoformat()}")


def test_until_takes_an_explicit_instant_and_requires_one():
    now = datetime(2026, 5, 1, 9, 0, tzinfo=NY)
    exp = core.dismissal_expiry("until", now, "2026-05-01T13:00:00-04:00")
    assert core.dismissal_active(exp, now)
    assert not core.dismissal_active(exp, now.replace(hour=14))
    with pytest.raises(ValueError):
        core.dismissal_expiry("until", now)


def test_an_unknown_scope_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        core.dismissal_expiry("a-while", datetime(2026, 5, 1, tzinfo=NY))


# --- is it still hiding? -----------------------------------------------------

def test_a_null_expiry_is_forever():
    assert core.dismissal_active(None, datetime(2099, 1, 1, tzinfo=NY))


@pytest.mark.parametrize("junk", ["", "soon", "2026-13-45", [], {}, "not a date"])
def test_an_unparseable_expiry_fails_towards_showing_you_the_item(junk):
    """The safe direction. Hiding something because a timestamp could not be
    read is invisible data loss; showing it again is a nuisance you can see."""
    assert core.dismissal_active(junk, datetime(2026, 5, 1, tzinfo=NY)) is False


def test_a_naive_expiry_is_read_in_the_users_zone_not_utc():
    """Reading a naive 8pm as UTC would expire an evening snooze at 4pm."""
    now = datetime(2026, 5, 1, 19, 0, tzinfo=NY)
    assert core.dismissal_active("2026-05-01T20:00:00", now)
    assert not core.dismissal_active("2026-05-01T18:00:00", now)


def test_expiry_compares_in_real_time_across_zones():
    now = datetime(2026, 5, 1, 19, 0, tzinfo=NY)          # 23:00Z
    assert core.dismissal_active("2026-05-01T23:30:00+00:00", now)
    assert not core.dismissal_active("2026-05-01T22:30:00+00:00", now)


# --- the set, and filtering --------------------------------------------------

def test_active_dismissals_keeps_only_what_is_still_hiding():
    now = datetime(2026, 5, 1, 12, 0, tzinfo=NY)
    rows = [
        row("email:a", None),                                   # forever
        row("email:b", "2026-05-01T18:00:00-04:00"),            # later today
        row("email:c", "2026-05-01T09:00:00-04:00"),            # already over
        row("gym", "bad-timestamp"),                            # unreadable
    ]
    assert core.active_dismissals(rows, now) == {"email:a", "email:b"}


@pytest.mark.parametrize("rows", [None, [], [{}], ["nope", 7], [{"key": None}]])
def test_garbage_rows_hide_nothing(rows):
    assert core.active_dismissals(rows, datetime(2026, 5, 1, tzinfo=NY)) == set()


def test_apply_dismissals_removes_only_the_dismissed():
    now = datetime(2026, 5, 1, 12, 0, tzinfo=NY)
    items = [{"key": "email:a"}, {"key": "email:b"}, {"key": "gym"}]
    rows = [row("email:b", None)]
    assert core.apply_dismissals(items, rows, now) == [
        {"key": "email:a"}, {"key": "gym"}]


def test_an_item_with_no_key_can_never_be_hidden():
    """A missing key is not a licence to hide it. Anything unidentifiable has
    to stay visible or it disappears with no way to bring it back."""
    now = datetime(2026, 5, 1, 12, 0, tzinfo=NY)
    items = [{"title": "no key here"}, {"key": ""}, {"key": None}]
    rows = [row("", None), row(None, None)]
    assert core.apply_dismissals(items, rows, now) == items


def test_a_dismissal_filters_rather_than_deletes():
    """The row survives its own expiry, because "you have snoozed this six
    times" is a fact about you that only exists while the row does."""
    now = datetime(2026, 5, 2, 12, 0, tzinfo=NY)
    rows = [row("gym", "2026-05-01T00:00:00-04:00", count=6)]
    assert core.active_dismissals(rows, now) == set()   # no longer hiding
    assert rows[0]["count"] == 6                        # but still on record


def test_the_expiry_and_the_check_agree_over_a_calendar_sweep():
    """The two halves are used together and are swept together: something
    snoozed for today is hidden all day and shown the next morning, on all
    800 days and at every hour."""
    start = datetime(2026, 1, 1, tzinfo=NY)
    for day in range(0, 800, 7):
        for hour in (0, 9, 17, 23):
            now = (start + timedelta(days=day)).replace(hour=hour)
            exp = core.dismissal_expiry("today", now)
            assert core.dismissal_active(exp, now), f"not hidden at {now}"
            later = now.replace(hour=23, minute=59)
            if later > now:
                assert core.dismissal_active(exp, later), f"unhid early at {later}"
            nxt = (now + timedelta(days=1)).replace(hour=8)
            assert not core.dismissal_active(exp, nxt), f"still hidden at {nxt}"
