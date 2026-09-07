"""The portfolio card said "Today" about data it could not know was today.

Both quote sources are daily bars. During market hours the difference of the
last two rows is usually the PREVIOUS completed session; on a Saturday it is
Friday-vs-Thursday; over a long weekend it is three days old. All of it was
rendered as "Today · +$212" with no as-of date anywhere to contradict it.

docs/BUDGETS.md would not permit that label in the money layer, and this is
the same rule reaching the portfolio. So the invariant these tests protect is
blunt: a completed past session is NEVER called "Today".

Date logic, so per docs/TESTING.md it is swept across a calendar rather than
run against whatever day the suite happens to execute on.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

st = load_service_module("stocks_main", "services/stocks/app/main.py")
session_label = st.session_label

CALENDAR = [date(2026, 1, 1) + timedelta(days=i) for i in range(365 * 2 + 1)]


@pytest.mark.parametrize("today", CALENDAR, ids=lambda d: d.isoformat())
def test_a_past_session_is_never_called_today(today):
    """THE invariant. Every day of two years, every lag from 1 to 10 days."""
    for lag in range(1, 11):
        label = session_label(today - timedelta(days=lag), today)
        assert label is not None
        assert label != "Today", f"{lag}d-old session called Today on {today}"


@pytest.mark.parametrize("today", CALENDAR, ids=lambda d: d.isoformat())
def test_the_same_session_is_called_today(today):
    assert session_label(today, today) == "Today"


def test_a_weekend_shows_the_friday_it_actually_came_from():
    """The case from the report: open it on a Sunday, and the card should say
    which session it is showing rather than 'Today'."""
    sunday = date(2026, 9, 6)
    friday = date(2026, 9, 4)
    assert sunday.strftime("%a") == "Sun" and friday.strftime("%a") == "Fri"
    assert session_label(friday, sunday) == "Fri close"


def test_a_long_weekend_is_still_named_not_rounded_away():
    tuesday, friday = date(2026, 9, 8), date(2026, 9, 4)
    assert session_label(friday, tuesday) == "Fri close"


def test_yesterday_is_named_plainly():
    assert session_label(date(2026, 9, 6), date(2026, 9, 7)) == "Yesterday's close"


def test_beyond_a_week_falls_back_to_a_date():
    """Past seven days a weekday name is ambiguous — "Fri close" could be any
    Friday — so it becomes an actual date."""
    assert session_label(date(2026, 8, 28), date(2026, 9, 7)) == "28 Aug close"


@pytest.mark.parametrize("bad", [None, "", "garbage", "2026-13-45", 12345])
def test_an_unreadable_as_of_is_unknown_not_a_guess(bad):
    """None => the card says the as-of date is unavailable. It must never
    fall back to claiming Today."""
    assert session_label(bad, date(2026, 9, 7)) is None


def test_a_future_dated_quote_does_not_go_backwards():
    """Clock skew between the provider and the box must not produce
    'in 1 day close'."""
    assert session_label(date(2026, 9, 8), date(2026, 9, 7)) == "Today"


def test_the_clock_seam_exists_and_is_patchable(monkeypatch):
    """docs/TESTING.md's one-clock rule, which had not reached this service."""
    monkeypatch.setattr(st, "_today", lambda: date(2026, 9, 7))
    assert st._today() == date(2026, 9, 7)


def test_no_bare_clock_call_outside_the_seam():
    """The same assertion firefly carries: date logic goes through _today()."""
    src = Path(st.__file__).read_text()
    body = src.split("def _today()", 1)[1].split("def session_label", 1)[0]
    rest = src.replace(body, "")
    assert "date.today()" not in rest, "a bare date.today() reappeared"
    # datetime.now is allowed only inside the seam itself
    assert rest.count("datetime.now(") == 0, "a bare datetime.now() reappeared"


# ---- the portfolio headline, across a mix of quotes ----------------------

portfolio_session = st.portfolio_session
TODAY = date(2026, 9, 7)


def q(as_of, intraday=False):
    return {"as_of": as_of, "intraday": intraday}


def test_one_stale_symbol_makes_the_whole_headline_a_close():
    """The blend problem: a portfolio is only as current as its oldest quote,
    so a single end-of-day bar must not be reported under a live label."""
    s = portfolio_session([q("2026-09-07", intraday=True), q("2026-09-04")], TODAY)
    assert s["intraday"] is False
    assert s["as_of"] == "2026-09-04"
    assert s["label"] == "Fri close"


def test_all_intraday_is_the_only_way_to_earn_today():
    s = portfolio_session([q("2026-09-07", intraday=True),
                           q("2026-09-07", intraday=True)], TODAY)
    assert s["intraday"] is True and s["label"] == "Today"


def test_all_end_of_day_takes_the_oldest():
    s = portfolio_session([q("2026-09-04"), q("2026-09-03"), q("2026-09-04")], TODAY)
    assert s["as_of"] == "2026-09-03" and s["label"] == "Thu close"


def test_no_quotes_is_unknown_not_today():
    s = portfolio_session([], TODAY)
    assert s["as_of"] is None and s["intraday"] is False and s["label"] is None


def test_quotes_without_an_as_of_do_not_invent_one():
    s = portfolio_session([q(None), q(None)], TODAY)
    assert s["as_of"] is None and s["label"] is None


def test_failed_symbols_are_ignored_rather_than_treated_as_stale():
    """None entries are unpriced positions, not old ones."""
    s = portfolio_session([None, q("2026-09-07", intraday=True)], TODAY)
    assert s["label"] == "Today"
