"""Recurring-charge detection: what it finds, and — mostly — what it refuses.

The value of this feature is entirely in its false-positive rate. A card that
announces "NEW SUBSCRIPTION" every time you buy coffee twice in a fortnight
gets ignored within a week, and an ignored card is worse than no card, because
the one month something real appears you will scroll past it too.

So most of what follows asserts a refusal. Per docs/TESTING.md the date-shaped
claims are swept across a calendar rather than asserted on one hand-picked day:
"appeared" and "resumed" are recency claims, and a recency claim that only
works in September is a bug waiting for October.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.recurring import (  # noqa: E402
    APPEARED_WINDOW_DAYS,
    CADENCES,
    detect_recurring,
    display_name,
    merchant_key,
)

# A two-year sweep, sampled weekly: enough to cross every month boundary, both
# year rollovers and a leap day without making the suite slow.
SWEEP = [date(2025, 1, 1) + timedelta(days=7 * i) for i in range(105)]


def charge(d, name, amount, desc=None):
    return {"date": d.isoformat() if isinstance(d, date) else d,
            "desc": desc if desc is not None else name,
            "destination": name if desc is None else "",
            "amount": amount, "category": "Uncategorized"}


def series(name, first, count, every, amount, desc=None):
    """`count` charges starting at `first`, one every `every` days."""
    return [charge(first + timedelta(days=every * i), name, amount, desc)
            for i in range(count)]


def run(charges, today, window_days=400, **kw):
    return detect_recurring(charges, today, today - timedelta(days=window_days), **kw)


def events_of(out, kind):
    return [e for e in out["events"] if e["event"] == kind]


# --- the three things it exists to say ---------------------------------------

@pytest.mark.parametrize("today", SWEEP)
def test_a_new_subscription_is_announced_on_any_day_of_the_year(today):
    """The headline case, swept: three monthly charges starting two months ago,
    with most of a year of readable history before them."""
    out = run(series("Anthropic", today - timedelta(days=61), 3, 30, 20.0), today)
    got = events_of(out, "appeared")
    assert [e["name"] for e in got] == ["Anthropic"]
    assert got[0]["amount"] == 20.0
    assert got[0]["cadence"] == "monthly"


@pytest.mark.parametrize("today", SWEEP)
def test_a_price_rise_is_announced_with_both_prices(today):
    rows = series("Netflix", today - timedelta(days=390), 13, 30, 15.49)
    rows[-1]["amount"] = 17.99
    got = events_of(run(rows, today), "changed")
    assert len(got) == 1
    assert (got[0]["from"], got[0]["to"]) == (15.49, 17.99)


@pytest.mark.parametrize("today", SWEEP)
def test_a_charge_after_a_long_gap_reads_as_resumed(today):
    """The cancellation that wasn't: monthly for four months, silent for nine,
    then charged again this week."""
    rows = series("Planet Fitness", today - timedelta(days=380), 4, 30, 24.99)
    rows += [charge(today - timedelta(days=3), "Planet Fitness", 24.99)]
    got = events_of(run(rows, today), "resumed")
    assert len(got) == 1
    assert got[0]["gap_days"] > 60


@pytest.mark.parametrize("today", SWEEP)
def test_a_resumption_that_is_no_longer_recent_is_not_an_announcement(today):
    """It restarted, charged once, and went quiet again seven months ago.
    The gap is still in the history and always will be; without a freshness
    check the card would announce "this came back!" every day forever."""
    rows = series("Planet Fitness", today - timedelta(days=390), 4, 30, 24.99)
    rows += [charge(today - timedelta(days=200), "Planet Fitness", 24.99)]
    out = run(rows, today)
    assert [i["name"] for i in out["items"]] == ["Planet Fitness"]
    assert events_of(out, "resumed") == []


# --- the refusals ------------------------------------------------------------

@pytest.mark.parametrize("today", SWEEP)
def test_a_merchant_you_simply_use_often_is_not_a_subscription(today):
    """Four coffees at irregular intervals. No cadence, so no claim at all —
    not a low-confidence one, not an item in the inventory."""
    days = [40, 33, 19, 2]
    rows = [charge(today - timedelta(days=n), "Starbucks", 6.25) for n in days]
    out = run(rows, today)
    assert out["items"] == []
    assert out["events"] == []


@pytest.mark.parametrize("today", SWEEP)
def test_an_annual_renewal_seen_twice_is_never_called_new(today):
    """The signature false positive of this class of feature. Two charges a
    year apart is a real annual pattern, and it is still not news."""
    rows = series("Insurance Co", today - timedelta(days=370), 2, 365, 890.0)
    out = run(rows, today, window_days=1200)
    assert [i["cadence"] for i in out["items"]] == ["annual"]
    assert events_of(out, "appeared") == []


@pytest.mark.parametrize("today", SWEEP)
def test_the_oldest_thing_visible_is_not_the_same_as_a_new_thing(today):
    """A subscription whose first charge sits at the edge of the read. We
    cannot see whether it was charging before, so we do not say it wasn't."""
    rows = series("Spotify", today - timedelta(days=88), 3, 30, 11.99)
    out = run(rows, today, window_days=90)   # 2 days of history before it
    assert [i["name"] for i in out["items"]] == ["Spotify"]
    assert out["items"][0]["history_before_days"] == 2
    assert events_of(out, "appeared") == []


@pytest.mark.parametrize("today", SWEEP)
def test_new_stops_being_news(today):
    """Same subscription, older. There is no day on which a year-old
    subscription is an announcement."""
    rows = series("Anthropic", today - timedelta(days=330), 12, 30, 20.0)
    assert events_of(run(rows, today), "appeared") == []


@pytest.mark.parametrize("today", SWEEP)
def test_a_truncated_read_makes_no_claim_about_absence(today):
    """`complete=False` means Firefly had more than we read. Everything we did
    read still stands — but 'this is new' and 'this came back' are both claims
    about what ISN'T in the data, and a partial read cannot make them."""
    new = series("Anthropic", today - timedelta(days=61), 3, 30, 20.0)
    back = (series("Planet Fitness", today - timedelta(days=380), 4, 30, 24.99)
            + [charge(today - timedelta(days=3), "Planet Fitness", 24.99)])
    priced = series("Netflix", today - timedelta(days=390), 13, 30, 15.49)
    priced[-1]["amount"] = 17.99
    out = run(new + back + priced, today, complete=False)
    assert events_of(out, "appeared") == []
    assert events_of(out, "resumed") == []
    assert len(events_of(out, "changed")) == 1     # two charges we did read
    assert out["absence_claims_suppressed"] is True
    assert len(out["items"]) == 3                  # the inventory survives


@pytest.mark.parametrize("today", SWEEP)
def test_a_bill_you_already_declared_is_not_a_discovery(today):
    rows = series("Verizon", today - timedelta(days=61), 3, 30, 85.0)
    out = run(rows, today, known_bills=[{"name": "Verizon", "amount": 85.0}])
    assert out["items"][0]["known_bill"] is True
    assert events_of(out, "appeared") == []


@pytest.mark.parametrize("today", SWEEP)
def test_a_penny_of_drift_is_not_a_price_change(today):
    rows = series("Netflix", today - timedelta(days=390), 13, 30, 15.49)
    rows[-1]["amount"] = 15.50
    assert events_of(run(rows, today), "changed") == []


@pytest.mark.parametrize("today", SWEEP)
def test_a_cheap_charge_needs_a_real_dollar_move_not_just_a_percentage(today):
    """2% of $1.99 is four cents. The absolute floor is what stops a card
    telling you your iCloud bill 'changed price'."""
    rows = series("iCloud", today - timedelta(days=390), 13, 30, 1.99)
    rows[-1]["amount"] = 2.09          # 5%, but a dime
    assert events_of(run(rows, today), "changed") == []
    rows[-1]["amount"] = 2.99          # a real move
    assert len(events_of(run(rows, today), "changed")) == 1


@pytest.mark.parametrize("today", SWEEP)
def test_one_promotional_month_does_not_redefine_the_price(today):
    """A single $0 month in the middle, then back to normal. The established
    amount is what it charges, not what it charged once."""
    rows = series("Hulu", today - timedelta(days=390), 13, 30, 17.99)
    rows[0]["amount"] = 0.99      # introductory month
    rows[6]["amount"] = 0.99      # a win-back offer later on
    out = run(rows, today)
    # Not the mean ($15.37), not the first charge ($0.99) — the mode.
    assert out["items"][0]["established_amount"] == 17.99
    assert events_of(out, "changed") == []


@pytest.mark.parametrize("today", SWEEP)
def test_a_single_charge_is_never_a_pattern(today):
    out = run([charge(today - timedelta(days=5), "Some Shop", 60.0)], today)
    assert out["items"] == []


@pytest.mark.parametrize("today", SWEEP)
def test_future_dated_rows_are_dropped_rather_than_counted(today):
    """Firefly's window is asked one day long to avoid a zero-length range, so
    a same-day scheduled charge can come back dated tomorrow."""
    rows = series("Netflix", today - timedelta(days=60), 3, 30, 15.49)
    rows += [charge(today + timedelta(days=5), "Netflix", 99.0)]
    out = run(rows, today)
    assert out["items"][0]["charges"] == 3
    assert out["items"][0]["latest_amount"] == 15.49


@pytest.mark.parametrize("today", SWEEP)
def test_rows_before_the_declared_window_do_not_secretly_widen_it(today):
    """window_start is the caller's statement of what it read. A row older
    than it would make 'appeared' judge itself against history it did not
    actually declare."""
    rows = series("Spotify", today - timedelta(days=88), 3, 30, 11.99)
    rows += [charge(today - timedelta(days=300), "Spotify", 11.99)]
    out = run(rows, today, window_days=90)
    assert out["items"][0]["charges"] == 3
    assert events_of(out, "appeared") == []


# --- the arithmetic ----------------------------------------------------------

@pytest.mark.parametrize("today", SWEEP)
def test_a_missed_month_does_not_destroy_the_cadence(today):
    """Skipping one payment is not proof you were never subscribed. A gap near
    a whole multiple of the rhythm is forgiven; anything else is not."""
    rows = (series("Adobe", today - timedelta(days=300), 5, 30, 22.99)
            + series("Adobe", today - timedelta(days=90), 4, 30, 22.99))
    out = run(rows, today)
    assert [i["cadence"] for i in out["items"]] == ["monthly"]


@pytest.mark.parametrize("today", SWEEP)
def test_monthly_equivalent_normalises_across_cadences(today):
    rows = (series("Netflix", today - timedelta(days=360), 12, 30, 20.0)
            + series("Domain Co", today - timedelta(days=380), 3, 91, 30.0))
    out = run(rows, today, window_days=1200)
    # $20/month + $30/quarter == 20 + 30*(30/91)
    assert out["monthly_equivalent"] == pytest.approx(20 + 30 * 30 / 91, abs=0.01)


@pytest.mark.parametrize("today", SWEEP)
def test_a_low_confidence_pattern_is_listed_but_not_costed(today):
    """Two charges set a cadence but not a budget line. Excluded from the
    monthly total rather than estimated into it."""
    rows = series("Maybe Co", today - timedelta(days=35), 2, 30, 40.0)
    out = run(rows, today)
    assert out["items"][0]["confidence"] == "low"
    assert out["monthly_equivalent"] == 0.0


@pytest.mark.parametrize("today", SWEEP)
def test_the_amount_shown_follows_a_price_change(today):
    """After a rise, 'what this costs' is the new price — the old one is
    history, and it is kept alongside rather than shown as the number."""
    rows = series("Netflix", today - timedelta(days=390), 13, 30, 15.49)
    rows[-1]["amount"] = 17.99
    item = run(rows, today)["items"][0]
    assert (item["amount"], item["established_amount"]) == (17.99, 15.49)


@pytest.mark.parametrize("today", SWEEP)
def test_next_expected_is_one_cadence_past_the_last_charge(today):
    rows = series("Netflix", today - timedelta(days=60), 3, 30, 15.49)
    item = run(rows, today)["items"][0]
    assert item["next_expected"] == (date.fromisoformat(item["last_seen"])
                                     + timedelta(days=30)).isoformat()


@pytest.mark.parametrize("today", SWEEP)
def test_no_charges_at_all_is_answered_without_inventing_one(today):
    out = run([], today)
    assert out == {"available": True, "complete": True,
                   "window_start": (today - timedelta(days=400)).isoformat(),
                   "items": [], "events": [], "monthly_equivalent": 0.0,
                   "absence_claims_suppressed": False}


# --- naming ------------------------------------------------------------------

def test_statement_noise_does_not_split_one_merchant_into_many():
    keys = {merchant_key({"desc": d}) for d in
            ("SQ *COFFEE 09/14", "SQ *COFFEE 10/14", "SQ *COFFEE #4821")}
    assert len(keys) == 1


def test_fireflys_account_name_wins_over_statement_text():
    t = {"destination": "Netflix", "desc": "NETFLIX.COM 4821 LOS GATOS CA"}
    assert merchant_key(t) == "netflix"
    assert display_name(t) == "Netflix"


def test_a_bare_description_is_cleaned_for_display_not_invented():
    t = {"destination": "", "desc": "CLAUDE AI SUB *4821"}
    assert display_name(t) == "CLAUDE AI SUB"


def test_two_different_merchants_do_not_merge():
    a = merchant_key({"desc": "NETFLIX.COM"})
    b = merchant_key({"desc": "NETFLIX GAMES"})
    assert a != b


# --- the cadence table itself ------------------------------------------------

@pytest.mark.parametrize("name,low,high,nominal", CADENCES)
def test_every_declared_cadence_is_actually_detectable(name, low, high, nominal):
    """A band in the table that no real series can land in is dead code
    pretending to be a feature."""
    today = date(2026, 6, 15)
    rows = series("Thing", today - timedelta(days=nominal * 3), 4, nominal, 9.99)
    out = run(rows, today, window_days=nominal * 6 + 30)
    assert [i["cadence"] for i in out["items"]] == [name]


@pytest.mark.parametrize("name,low,high,nominal", CADENCES)
def test_only_cadences_that_fit_the_news_window_can_be_new(name, low, high, nominal):
    """'Appeared' needs two charges inside the window in which new is still
    news. Quarterly and annual cannot manage it, and that is the point.

    Spaced at the BAND's low edge, not its nominal: a quarterly pair 82 days
    apart is 82 days old, which slips under a 90-day recency check on its own.
    Recency alone is therefore not enough to close the slow-cadence trap, and
    this is the case that proves the cadence rule is doing work.
    """
    today = date(2026, 6, 15)
    rows = series("Thing", today - timedelta(days=low), 2, low, 9.99)
    out = run(rows, today, window_days=nominal * 6 + 30)
    expected = nominal <= APPEARED_WINDOW_DAYS
    assert bool(events_of(out, "appeared")) is expected
