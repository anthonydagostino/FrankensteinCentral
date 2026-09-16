"""Amex credit periods, and what is about to expire.

WHY THIS FILE EXISTS: the whole feature is a deadline calculator. Every credit
here resets on a CALENDAR boundary and does not roll over, so getting a period
edge wrong does not produce a slightly-wrong number — it tells you a credit is
safe on the day it dies, or says you already used one you have not.

Per docs/TESTING.md nothing is checked against "today": the boundary rules are
swept across years, including the 31sts, the year end and the leap day, which
are exactly where this arithmetic breaks.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from conftest import load_service_module  # noqa: E402

cr = load_service_module("amex_credits", "services/amex/app/credits.py")


def every_day(start=date(2026, 1, 1), days=1200):
    for offset in range(days):
        yield start + timedelta(days=offset)


# --- the catalogue itself ----------------------------------------------------

def test_both_cards_are_present_with_their_fees():
    assert cr.CARDS["platinum"]["annual_fee"] == 895.00
    assert cr.CARDS["gold"]["annual_fee"] == 325.00


def test_every_credit_is_well_formed():
    """A row missing a cadence would crash the period maths at render time,
    on someone's dashboard, rather than here."""
    seen = set()
    for c in cr.ALL_CREDITS:
        assert c["cadence"] in cr.CADENCES, c
        assert c["amount"] > 0, c
        assert c["card"] in cr.CARDS, c
        assert c["name"] and c["where"], c
        assert c["key"] not in seen, f"duplicate key {c['key']}"
        seen.add(c["key"])


def test_the_annual_maximum_is_derived_unless_the_amount_varies_in_the_year():
    """Derived rather than typed, so a $10 monthly credit cannot be advertised
    as $100 a year because someone fat-fingered it.

    A credit may override that — the Platinum's Uber Cash is $15 a month and
    $35 in December — but ONLY if its per-period amount really does vary. An
    override on a flat credit is just the typo this test was written to catch,
    wearing a keyword argument.
    """
    for c in cr.ALL_CREDITS:
        derived = round(c["amount"] * cr.PERIODS_PER_YEAR[c["cadence"]], 2)
        varies = any(cr.december_uber_bonus(c, date(2026, m, 1)) != c["amount"]
                     for m in range(1, 13))
        if not varies:
            assert c["annual_max"] == derived, (
                f'{c["key"]} pays the same every period but advertises '
                f'{c["annual_max"]} instead of {derived}')
        else:
            assert c["annual_max"] != derived, (
                f'{c["key"]} varies within the year, so the derived '
                f"{derived} cannot also be right")


def test_the_gold_credits_are_the_ones_the_gold_card_has():
    gold = {c["key"]: c for c in cr.CARDS["gold"]["credits"]}
    assert gold["gold_uber"]["amount"] == 10.00 and gold["gold_uber"]["cadence"] == cr.MONTHLY
    assert gold["gold_dining"]["amount"] == 10.00 and gold["gold_dining"]["cadence"] == cr.MONTHLY
    assert gold["gold_dunkin"]["amount"] == 7.00 and gold["gold_dunkin"]["cadence"] == cr.MONTHLY
    assert gold["gold_resy"]["amount"] == 50.00 and gold["gold_resy"]["cadence"] == cr.SEMIANNUAL


def test_the_platinum_quarterly_credits_are_quarterly():
    """Resy and lululemon are the two that catch people out: they are quarterly
    and sizeable, so a missed quarter is $175 gone."""
    plat = {c["key"]: c for c in cr.CARDS["platinum"]["credits"]}
    assert plat["plat_resy"]["cadence"] == cr.QUARTERLY and plat["plat_resy"]["amount"] == 100.00
    assert plat["plat_lululemon"]["cadence"] == cr.QUARTERLY and plat["plat_lululemon"]["amount"] == 75.00
    assert plat["plat_hotel"]["cadence"] == cr.SEMIANNUAL and plat["plat_hotel"]["amount"] == 300.00


def test_the_catalogue_says_when_it_was_last_checked():
    """These are terms Amex changes — the Saks credit was withdrawn mid-2026.
    A stale catalogue has to announce itself rather than be believed."""
    assert date.fromisoformat(cr.CATALOGUE_CHECKED)


def test_credits_needing_enrolment_are_marked():
    """An unenrolled credit pays nothing and looks exactly like one you chose
    not to use, which is the most expensive way to be confused."""
    need = {c["key"] for c in cr.ALL_CREDITS if c["enroll"]}
    for key in ("plat_resy", "plat_lululemon", "plat_airline", "gold_dining", "gold_dunkin"):
        assert key in need, key


# --- period boundaries -------------------------------------------------------

@pytest.mark.parametrize("cadence", list(cr.CADENCES))
def test_today_always_falls_inside_its_own_period(cadence):
    for day in every_day():
        start, end = cr.period_of(cadence, day)
        assert start <= day <= end, (cadence, day, start, end)


@pytest.mark.parametrize("cadence", list(cr.CADENCES))
def test_periods_never_overlap_and_never_leave_a_gap(cadence):
    """Consecutive days either share a period or sit either side of a clean
    boundary. A gap would be a day on which no credit exists at all."""
    previous = None
    for day in every_day():
        start, end = cr.period_of(cadence, day)
        if previous is not None:
            p_start, p_end = previous
            same = (start, end) == (p_start, p_end)
            assert same or start == p_end + timedelta(days=1), (cadence, day)
        previous = (start, end)


@pytest.mark.parametrize("cadence", list(cr.CADENCES))
def test_days_left_counts_today_and_hits_one_on_the_last_day(cadence):
    """Inclusive, because that is how a person reads a deadline: on the last
    day of the month you have one day left, not none."""
    for day in every_day():
        _, end = cr.period_of(cadence, day)
        left = cr.days_left(cadence, day)
        assert left >= 1, (cadence, day)
        assert left == (end - day).days + 1
        if day == end:
            assert left == 1, (cadence, day)


def test_the_quarters_are_the_calendar_quarters():
    """Not the account anniversary. 1 Jan, 1 Apr, 1 Jul, 1 Oct."""
    for month, (s, e) in {1: (1, 3), 2: (1, 3), 3: (1, 3), 4: (4, 6), 5: (4, 6),
                          6: (4, 6), 7: (7, 9), 8: (7, 9), 9: (7, 9),
                          10: (10, 12), 11: (10, 12), 12: (10, 12)}.items():
        start, end = cr.period_of(cr.QUARTERLY, date(2026, month, 15))
        assert (start.month, end.month) == (s, e), month
        assert start.day == 1


def test_month_ends_are_right_including_february():
    assert cr.period_of(cr.MONTHLY, date(2026, 2, 10))[1] == date(2026, 2, 28)
    assert cr.period_of(cr.MONTHLY, date(2028, 2, 10))[1] == date(2028, 2, 29)  # leap
    assert cr.period_of(cr.MONTHLY, date(2026, 12, 3))[1] == date(2026, 12, 31)
    assert cr.period_of(cr.MONTHLY, date(2026, 4, 30))[1] == date(2026, 4, 30)


def test_the_last_day_of_december_is_one_day_left_not_a_rollover():
    """The single most expensive day of the year to get wrong: every annual
    credit dies at midnight and none of them roll over."""
    nye = date(2026, 12, 31)
    for cadence in cr.CADENCES:
        assert cr.days_left(cadence, nye) == 1, cadence
        assert cr.period_of(cadence, nye)[1] == nye


@pytest.mark.parametrize("cadence", list(cr.CADENCES))
def test_period_ids_change_exactly_when_the_period_does(cadence):
    """The id is what redemptions are keyed on. If it changed a day early, a
    credit would look unused on its last day — the worst possible day to be
    told you still have it."""
    for day in every_day():
        start, _ = cr.period_of(cadence, day)
        assert cr.period_id(cadence, day) == cr.period_id(cadence, start), (cadence, day)
        previous = start - timedelta(days=1)
        assert cr.period_id(cadence, previous) != cr.period_id(cadence, day), (cadence, day)


def test_period_ids_read_the_way_a_person_would_write_them():
    assert cr.period_id(cr.MONTHLY, date(2026, 9, 16)) == "2026-M09"
    assert cr.period_id(cr.QUARTERLY, date(2026, 9, 16)) == "2026-Q3"
    assert cr.period_id(cr.SEMIANNUAL, date(2026, 9, 16)) == "2026-H2"
    assert cr.period_id(cr.ANNUAL, date(2026, 9, 16)) == "2026-Y"


def test_an_unknown_cadence_raises_rather_than_guessing():
    for fn in (cr.period_of, cr.period_id, cr.days_left):
        with pytest.raises(ValueError):
            fn("fortnightly", date(2026, 5, 5))


# --- the December Uber quirk -------------------------------------------------

def test_the_platinum_uber_credit_is_thirty_five_dollars_in_december():
    """Amex advertises $200 a year and 15 x 12 is 180. The extra $20 lands in
    December, and a tracker showing $15 would under-report the one month where
    being wrong costs the most."""
    uber = cr.BY_KEY["plat_uber"]
    assert cr.december_uber_bonus(uber, date(2026, 12, 1)) == 35.00
    for month in range(1, 12):
        assert cr.december_uber_bonus(uber, date(2026, month, 1)) == 15.00
    total = sum(cr.december_uber_bonus(uber, date(2026, m, 1)) for m in range(1, 13))
    assert total == 200.00, "the year should add up to the advertised $200"


def test_the_bonus_applies_to_no_other_credit():
    for c in cr.ALL_CREDITS:
        if c["key"] == "plat_uber":
            continue
        assert cr.december_uber_bonus(c, date(2026, 12, 15)) == c["amount"], c["key"]


# --- the summary: what is about to expire ------------------------------------

def test_nothing_marked_means_everything_is_available_and_nothing_captured():
    s = cr.summarise(date(2026, 9, 16), set())
    assert s["captured_this_period"] == 0
    assert s["available"] > 0
    assert all(not r["used"] for r in s["rows"])


def test_marking_one_period_used_does_not_touch_the_next():
    """The bug this prevents: a credit marked used in September still reading
    as used in October, so you quietly stop using it."""
    sept = date(2026, 9, 16)
    key = f"gold_dunkin:{cr.period_id(cr.MONTHLY, sept)}"
    rows = {r["key"]: r for r in cr.summarise(sept, {key})["rows"]}
    assert rows["gold_dunkin"]["used"] is True

    october = date(2026, 10, 16)
    rows = {r["key"]: r for r in cr.summarise(october, {key})["rows"]}
    assert rows["gold_dunkin"]["used"] is False, "September's mark leaked into October"


def test_at_risk_is_only_what_is_both_unused_and_nearly_expired():
    """The headline number. Counting a used credit, or one with three weeks
    left, would make it noise — and a number you learn to ignore is worse than
    no number."""
    # Last day of a month: every monthly credit has one day left.
    eom = date(2026, 8, 31)
    s = cr.summarise(eom, set())
    monthly = [r for r in s["rows"] if r["cadence"] == cr.MONTHLY]
    assert monthly and all(r["urgent"] for r in monthly)
    assert s["at_risk"] == round(sum(r["amount"] for r in s["rows"] if r["urgent"]), 2)

    # Mark them all used and the risk goes away without the credits doing so.
    used = {f"{r['key']}:{r['period_id']}" for r in monthly}
    s2 = cr.summarise(eom, used)
    assert s2["at_risk"] < s["at_risk"]
    assert all(not r["urgent"] for r in s2["rows"] if r["cadence"] == cr.MONTHLY)


def test_mid_period_nothing_is_urgent():
    """Early in a month with a long quarter ahead, the card should be quiet."""
    s = cr.summarise(date(2026, 5, 6), set())
    assert s["at_risk"] == 0
    assert not any(r["urgent"] for r in s["rows"])


def test_the_soonest_to_expire_is_listed_first():
    """The list is a queue of things to do, not a catalogue."""
    s = cr.summarise(date(2026, 3, 29), set())
    unused = [r for r in s["rows"] if not r["used"]]
    assert unused == sorted(unused, key=lambda r: (r["days_left"], -r["amount"]))
    assert all(r["used"] for r in s["rows"][len(unused):])


def test_used_credits_sort_below_unused_ones():
    day = date(2026, 3, 29)
    s_all = cr.summarise(day, set())
    used = {f"{s_all['rows'][0]['key']}:{s_all['rows'][0]['period_id']}"}
    s = cr.summarise(day, used)
    flags = [r["used"] for r in s["rows"]]
    assert flags == sorted(flags), "a used credit is still in the queue"


def test_asking_for_one_card_returns_only_that_card():
    s = cr.summarise(date(2026, 9, 16), set(), cards=("gold",))
    assert {r["card"] for r in s["rows"]} == {"gold"}
    assert s["annual_fees"] == 325.00


def test_the_advertised_credit_value_exceeds_the_fees_for_both_cards():
    """Not a claim that the cards are worth it — a check that the catalogue is
    not missing most of a card. If this ever inverts, a credit was dropped."""
    s = cr.summarise(date(2026, 9, 16), set())
    assert s["annual_credit_value"] > s["annual_fees"]
    assert s["annual_fees"] == 895.00 + 325.00


def test_at_risk_never_exceeds_available():
    """At-risk is a subset of unused. Sweeping the year because the overlap
    between cadences changes shape at every boundary."""
    for day in every_day(days=760):
        s = cr.summarise(day, set())
        assert s["at_risk"] <= s["available"] + 0.001, day


def test_captured_plus_available_is_the_whole_period_whatever_is_marked():
    """No credit may be counted twice or fall out of both totals."""
    day = date(2026, 12, 20)
    everything = cr.summarise(day, set())
    total = everything["available"]
    all_keys = {f"{r['key']}:{r['period_id']}" for r in everything["rows"]}
    for used in (set(), {next(iter(all_keys))}, all_keys):
        s = cr.summarise(day, used)
        assert round(s["available"] + s["captured_this_period"], 2) == round(total, 2), used


def test_december_shows_the_bigger_uber_credit_in_the_summary():
    rows = {r["key"]: r for r in cr.summarise(date(2026, 12, 5), set())["rows"]}
    assert rows["plat_uber"]["amount"] == 35.00
    rows = {r["key"]: r for r in cr.summarise(date(2026, 11, 5), set())["rows"]}
    assert rows["plat_uber"]["amount"] == 15.00


def test_every_row_carries_its_period_so_the_ui_never_computes_dates():
    for day in (date(2026, 1, 1), date(2026, 6, 30), date(2026, 12, 31)):
        for r in cr.summarise(day, set())["rows"]:
            assert date.fromisoformat(r["period_start"]) <= day
            assert date.fromisoformat(r["period_end"]) >= day
            assert r["days_left"] >= 1


# ── captured_ytd: what the cards actually returned, against their fees ───────
# Every one of these is about NOT inventing a number. The advertised value of
# these credits is public and large; what matters is the part you drew.

def _r(key, pid, amount=None):
    return {"credit_key": key, "period_id": pid, "amount": amount}


def test_captured_ytd_with_nothing_marked_is_a_full_loss():
    out = cr.captured_ytd(2026, [])
    assert out["captured"] == 0.0
    assert out["counted"] == 0
    # Both fees, unrecovered. Not zero, not None — you genuinely paid this.
    assert out["net"] == -(895.00 + 325.00)


def test_a_marked_credit_counts_its_full_face_value_when_no_amount_was_given():
    out = cr.captured_ytd(2026, [_r("gold_dunkin", "2026-M03")])
    assert out["captured"] == 7.00
    assert out["per_card"]["gold"] == 7.00
    assert out["per_card"]["platinum"] == 0.0


def test_an_explicit_amount_beats_the_face_value():
    """A $25 credit you only drew $9 of returned $9. Rounding it up to the
    advertised figure is the exact dishonesty docs/BUDGETS.md forbids."""
    out = cr.captured_ytd(2026, [_r("plat_digital", "2026-M03", 9.0)])
    assert out["captured"] == 9.00


def test_a_zero_amount_is_zero_and_not_a_missing_amount():
    """Zero and unknown are different states. `amount=0` means you marked it
    used and drew nothing; it must not fall through to the face value."""
    out = cr.captured_ytd(2026, [_r("plat_digital", "2026-M03", 0.0)])
    assert out["captured"] == 0.0
    assert out["counted"] == 1


def test_another_years_redemptions_do_not_leak_in():
    rows = [_r("gold_dunkin", "2025-M03"), _r("gold_dunkin", "2026-M03")]
    assert cr.captured_ytd(2026, rows)["counted"] == 1
    assert cr.captured_ytd(2025, rows)["counted"] == 1


def test_a_year_prefix_match_is_not_a_substring_match():
    """"2026-M03" and "12026-M03" both contain "2026". Only one is this year."""
    assert cr.captured_ytd(2026, [_r("gold_dunkin", "12026-M03")])["counted"] == 0


def test_a_credit_retired_from_the_catalogue_is_skipped_not_invented_back():
    """Amex withdrew the Saks credit mid-2026. Rows for it survive in the
    table; the amount it was worth does not come back from a guess."""
    out = cr.captured_ytd(2026, [_r("plat_saks_retired", "2026-H1")])
    assert out["captured"] == 0.0
    assert out["counted"] == 0


def test_filtering_to_one_card_drops_the_other_cards_credits_and_fee():
    rows = [_r("gold_dunkin", "2026-M03"), _r("plat_digital", "2026-M03")]
    out = cr.captured_ytd(2026, rows, cards=("gold",))
    assert out["per_card"] == {"gold": 7.00}
    assert out["captured"] == 7.00
    assert out["annual_fees"] == 325.00


def test_every_period_id_the_service_can_mint_is_countable_by_year():
    """The join between period_id and captured_ytd, swept rather than assumed:
    if period_id ever stops leading with the year, this is what notices."""
    for day in every_day():
        for cadence in cr.CADENCES:
            pid = cr.period_id(cadence, day)
            counted = cr.captured_ytd(day.year, [_r("gold_dunkin", pid)])["counted"]
            assert counted == 1, f"{pid} on {day} was not counted under {day.year}"


def test_net_is_captured_minus_fees_for_every_shape_of_input():
    for rows in ([], [_r("gold_dunkin", "2026-M03")],
                 [_r("plat_oura", "2026-Y"), _r("plat_digital", "2026-M01", 3.5)]):
        out = cr.captured_ytd(2026, rows)
        assert out["net"] == round(out["captured"] - out["annual_fees"], 2)
        assert round(sum(out["per_card"].values()), 2) == out["captured"]


def test_every_annual_max_equals_a_real_year_of_that_credit():
    """The catalogue's advertised yearly figure, checked against twelve months
    of actual period amounts rather than believed.

    It is derived from the per-period amount for all but one credit, and that
    one — the Platinum's Uber Cash, $15 a month and $35 in December — is
    exactly why the derivation cannot be trusted on its own. A sum of $180 for
    a benefit Amex sells as $200 is the kind of error that looks right.
    """
    for credit in cr.ALL_CREDITS:
        seen, total = set(), 0.0
        for day in every_day(date(2026, 1, 1), 365):
            pid = cr.period_id(credit["cadence"], day)
            if pid in seen:
                continue
            seen.add(pid)
            total += cr.december_uber_bonus(credit, day)
        assert round(total, 2) == credit["annual_max"], (
            f'{credit["key"]}: a year of periods is {round(total, 2)}, but the '
            f'catalogue advertises {credit["annual_max"]}')
