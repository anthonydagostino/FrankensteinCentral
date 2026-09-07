"""Paycheck-cycle engine tests — pure functions, pinned dates.

The question this engine answers ("how much of my paycheck is left to
spend?") is wrong in a *quiet* way when it is wrong: it still prints a
plausible dollar figure. So these tests concentrate on the ways a plausible
number can be a false one:

  * a savings transfer counted as spending (or as both a transfer AND a
    deduction — subtracting the same $1,100 twice);
  * an "expected" deduction silently presented as one that actually
    happened;
  * a stale ledger producing a confident "left to spend" that is really
    "left to spend as of eight days ago";
  * a missed paycheck making the previous cycle look catastrophically
    overspent;
  * an empty month rendering as $0 spent rather than "unknown".

Date-dependent behaviour is swept across a calendar rather than tested on
one convenient day (docs/TESTING.md explains why).
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

pc = load_service_module("budget_paycheck", "services/budget/app/paycheck.py")
paycheck_cycle = pc.paycheck_cycle

CFG = {
    "enabled": True,
    "match": ["payroll"],
    "min_amount": 500,
    "cadence_days": 14,
    "allocations": [
        {"name": "Fidelity", "amount": 1100, "match": ["fidelity"]},
        {"name": "Marcus", "amount": 500, "match": ["marcus"]},
    ],
}


def txn(d, desc, amount, category="Uncategorized", source="", destination=""):
    return {"date": d if isinstance(d, str) else d.isoformat(), "desc": desc,
            "amount": amount, "category": category,
            "source": source, "destination": destination}


def month_of(today: date) -> dict:
    import calendar
    total = calendar.monthrange(today.year, today.month)[1]
    return {"label": today.strftime("%B %Y"), "start": today.replace(day=1).isoformat(),
            "days_total": total, "days_elapsed": today.day, "days_left": total - today.day}


def run(today, deposits=(), withdrawals=(), transfers=(), cfg=None,
        ingest_days=0, month_ingested=True, ledger_latest=None):
    return paycheck_cycle(
        cfg=CFG if cfg is None else cfg,
        today=today, month=month_of(today),
        deposits=list(deposits), withdrawals=list(withdrawals),
        transfers=list(transfers),
        freshness={"ingest_days": ingest_days, "activity_days": ingest_days,
                   "month_ingested": month_ingested,
                   "ledger_latest_txn": (ledger_latest or today).isoformat()},
    )


# A representative cycle: paid on the 28th, both transfers went out the next
# day, and $312 has been spent since.
def standard(today=date(2026, 9, 4)):
    return run(
        today,
        deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0, source="ACME Corp")],
        withdrawals=[txn(date(2026, 9, 1), "Groceries", 212.0, "Groceries"),
                     txn(date(2026, 9, 3), "Gas", 100.0, "Transportation")],
        transfers=[txn(date(2026, 8, 29), "Savings", 1100.0, destination="Fidelity Brokerage"),
                   txn(date(2026, 8, 29), "Savings", 500.0, destination="Marcus Savings")],
    )


# ---- the headline number ------------------------------------------------

def test_left_to_spend_is_paycheck_minus_savings_minus_spending():
    d = standard()["cycle"]
    assert d["paycheck"] == 2400.0
    assert d["savings_total"] == 1600.0
    assert d["spendable"] == 800.0
    assert d["spent"] == 312.0
    assert d["left"] == 488.0


def test_observed_transfers_are_labelled_as_observed():
    allocs = {a["name"]: a for a in standard()["cycle"]["allocations"]}
    assert allocs["Fidelity"]["source"] == "observed"
    assert allocs["Fidelity"]["observed"] == 1100.0
    assert allocs["Fidelity"]["date"] == "2026-08-29"


def test_a_transfer_to_savings_is_not_spending():
    """The whole point: moving $1,100 to Fidelity must not read as $1,100
    spent. If transfers leaked into `spent`, left would be $1,600 lower."""
    d = standard()["cycle"]
    assert d["spent"] == 312.0
    assert all(not t["savings"] for t in d["txns"])


def test_savings_booked_as_a_withdrawal_is_not_counted_twice():
    """Some imports book the Fidelity transfer as a plain withdrawal. It must
    be recognised as the deduction it is — not subtracted once as savings and
    again as spending."""
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            withdrawals=[txn(date(2026, 8, 29), "To Fidelity", 1100.0, "Savings"),
                         txn(date(2026, 8, 29), "Marcus deposit", 500.0, "Savings"),
                         txn(date(2026, 9, 1), "Groceries", 212.0, "Groceries")])["cycle"]
    assert d["savings_total"] == 1600.0
    assert d["spent"] == 212.0
    assert d["left"] == 588.0        # 2400 - 1600 - 212, the $1,100 subtracted once


def test_expected_deductions_are_never_presented_as_observed():
    """Payday landed but the transfers haven't been imported yet. The
    configured amounts still shape the number — that is the user's stated
    plan — but the UI must be able to say they're expected, not seen."""
    d = run(date(2026, 8, 29),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)])
    allocs = {a["name"]: a for a in d["cycle"]["allocations"]}
    assert allocs["Fidelity"]["source"] == "expected"
    assert allocs["Fidelity"]["observed"] is None
    assert allocs["Fidelity"]["amount"] == 1100.0
    assert d["cycle"]["spendable"] == 800.0


def test_partial_transfer_uses_what_actually_happened():
    """$900 of the planned $1,100 moved. The ledger wins over the plan."""
    d = run(date(2026, 9, 2),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            transfers=[txn(date(2026, 8, 29), "Savings", 900.0, destination="Fidelity")])["cycle"]
    allocs = {a["name"]: a for a in d["allocations"]}
    assert allocs["Fidelity"]["amount"] == 900.0
    assert allocs["Fidelity"]["planned"] == 1100.0
    assert d["savings_total"] == 1400.0     # 900 observed + 500 expected


def test_withheld_before_deposit_is_shown_but_not_subtracted():
    """If the employer takes it out before the money lands, the deposit is
    already net — subtracting it again would invent a $1,100 shortfall."""
    cfg = {**CFG, "allocations": [
        {"name": "Fidelity", "amount": 1100, "match": ["fidelity"],
         "already_withheld": True},
        {"name": "Marcus", "amount": 500, "match": ["marcus"]}]}
    d = run(date(2026, 8, 29), cfg=cfg,
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 1300.0)])["cycle"]
    allocs = {a["name"]: a for a in d["allocations"]}
    assert allocs["Fidelity"]["amount"] == 0.0
    assert allocs["Fidelity"]["planned"] == 1100.0
    assert d["savings_total"] == 500.0
    assert d["spendable"] == 800.0


def test_split_deposits_on_one_day_are_one_paycheck():
    d = run(date(2026, 9, 1),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 1400.0),
                      txn(date(2026, 8, 28), "ACME PAYROLL", 1000.0)])["cycle"]
    assert d["paycheck"] == 2400.0
    assert d["paycheck_parts"] == 2


def test_overspending_is_stated_plainly_not_clamped_to_zero():
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            transfers=[txn(date(2026, 8, 29), "Savings", 1100.0, destination="Fidelity"),
                       txn(date(2026, 8, 29), "Savings", 500.0, destination="Marcus")],
            withdrawals=[txn(date(2026, 9, 2), "Car repair", 950.0, "Transportation")])["cycle"]
    assert d["left"] == -150.0
    assert d["state"] == "over"
    assert "past" in d["text"]


# ---- freshness: as-of-the-ledger, never a same-day claim ----------------

def test_stale_ledger_keeps_totals_but_pauses_per_day_guidance():
    """The user's real situation: last import eight days ago. The totals are
    still true as of the ledger, so they show — but $/day would be advice
    built on a window with a hole in it."""
    d = run(date(2026, 9, 4), ingest_days=8,
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            transfers=[txn(date(2026, 8, 29), "Savings", 1100.0, destination="Fidelity"),
                       txn(date(2026, 8, 29), "Savings", 500.0, destination="Marcus")],
            withdrawals=[txn(date(2026, 8, 29), "Groceries", 212.0)],
            ledger_latest=date(2026, 8, 29))
    assert d["fresh"] is False
    assert "8 days" in d["stale_reason"]
    assert d["cycle"]["left"] == 588.0          # true as of the ledger
    assert d["cycle"]["per_day"] is None        # never guidance off stale data
    assert d["as_of"] == "2026-08-29"


def test_fresh_ledger_gives_per_day_guidance():
    d = standard()["cycle"]
    assert d["days_to_next"] == 7               # Aug 28 + 14 = Sep 11, from Sep 4
    assert d["per_day"] == round(488.0 / 7, 2)


def test_no_ingest_signal_is_treated_as_stale_not_as_fresh():
    d = run(date(2026, 9, 4), ingest_days=None,
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)])
    assert d["fresh"] is False
    assert d["cycle"]["per_day"] is None


def test_a_missing_paycheck_reports_unknown_rather_than_a_huge_overspend():
    """Three weeks past a biweekly payday with no new deposit: the ledger is
    behind, not the user $2,000 in the hole. Guessing here is how a money
    app loses trust."""
    d = run(date(2026, 9, 22), ingest_days=8,
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            withdrawals=[txn(date(2026, 9, 10), "Rent", 1500.0)])
    assert d["cycle"]["overdue"] is True
    assert d["cycle"]["left"] is None
    assert d["cycle"]["state"] == "unknown"
    assert "isn't in the ledger yet" in d["cycle"]["text"]


def test_payday_drift_within_the_grace_period_is_not_overdue():
    """Paydays slide across weekends; one or two days late is normal."""
    d = run(date(2026, 9, 13),   # expected Sep 11, two days ago
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)])
    assert d["cycle"]["overdue"] is False
    assert d["cycle"]["left"] is not None


# ---- month to date ------------------------------------------------------

def test_month_spend_excludes_savings_transfers():
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            withdrawals=[txn(date(2026, 9, 1), "Groceries", 212.0),
                         txn(date(2026, 9, 2), "To Fidelity", 1100.0, "Savings")],
            transfers=[txn(date(2026, 9, 2), "Savings", 500.0, destination="Marcus")])
    assert d["month"]["spent"] == 212.0
    assert d["month"]["savings"] == 1600.0


def test_month_spend_ignores_last_months_transactions():
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            withdrawals=[txn(date(2026, 8, 20), "August dinner", 90.0),
                         txn(date(2026, 9, 1), "Groceries", 212.0)])
    assert d["month"]["spent"] == 212.0


def test_empty_month_is_unknown_not_zero():
    d = run(date(2026, 9, 1), month_ingested=False,
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)])
    assert d["month"]["spent"] is None
    assert d["month"]["savings"] is None
    assert d["month"]["daily_avg"] is None


@pytest.mark.parametrize("today", [date(2026, 1, 1), date(2026, 2, 28), date(2028, 2, 29),
                                   date(2026, 4, 30), date(2026, 12, 31)])
def test_month_window_holds_on_awkward_days(today):
    """Month edges, a leap day, and a year boundary — a cycle that started in
    the previous month must still be one cycle."""
    pay_date = today - timedelta(days=6)
    d = run(today,
            deposits=[txn(pay_date, "ACME PAYROLL", 2400.0)],
            withdrawals=[txn(today, "Coffee", 5.0)])
    assert d["cycle"]["start"] == pay_date.isoformat()
    assert d["cycle"]["days_elapsed"] == 7
    assert d["month"]["spent"] == 5.0
    assert d["month"]["label"] == today.strftime("%B %Y")


# ---- cadence ------------------------------------------------------------

def test_cadence_is_learned_from_the_ledger_when_it_can_be():
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0),
                      txn(date(2026, 8, 21), "ACME PAYROLL", 2400.0)])["cycle"]
    assert d["cadence_days"] == 7
    assert d["cadence_source"] == "observed"
    assert d["next_payday"] == "2026-09-04"


def test_an_implausible_gap_falls_back_to_the_configured_cadence():
    """A six-month gap is a partially-imported ledger, not a pay schedule."""
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0),
                      txn(date(2026, 2, 28), "ACME PAYROLL", 2400.0)])["cycle"]
    assert d["cadence_days"] == 14
    assert d["cadence_source"] == "configured"


# ---- identifying the paycheck ------------------------------------------

def test_small_and_unmatched_deposits_are_not_paychecks():
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 9, 2), "Venmo from Sam", 60.0),
                      txn(date(2026, 9, 3), "Refund", 800.0)])
    assert d["available"] is False
    assert "no paycheck found" in d["reason"]
    # the month answer does not depend on finding a paycheck
    assert d["month"]["spent"] == 0


def test_the_month_still_answers_when_the_cycle_cannot():
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 9, 2), "Venmo from Sam", 60.0)],
            withdrawals=[txn(date(2026, 9, 2), "Groceries", 212.0)])
    assert d["available"] is False
    assert d["month"]["spent"] == 212.0


def test_account_names_identify_the_paycheck_when_the_description_does_not():
    cfg = {**CFG, "match": ["acme"]}
    d = run(date(2026, 9, 4), cfg=cfg,
            deposits=[txn(date(2026, 8, 28), "Deposit", 2400.0, source="ACME Corp")])
    assert d["available"] is True
    assert d["cycle"]["paycheck"] == 2400.0


def test_future_dated_deposits_are_ignored():
    """Firefly allows future-dated transactions; a paycheck that hasn't
    landed cannot be the one being spent."""
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0),
                      txn(date(2026, 9, 11), "ACME PAYROLL", 2400.0)])["cycle"]
    assert d["start"] == "2026-08-28"


def test_turning_it_off_is_not_an_error_state():
    d = run(date(2026, 9, 4), cfg={**CFG, "enabled": False},
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)])
    assert d["available"] is False
    assert d["configured"] is False


def test_no_config_produces_no_claims():
    d = run(date(2026, 9, 4), cfg={},
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)])
    assert d == {"configured": False, "available": False,
                 "reason": "paycheck tracking is off", "month": None, "cycle": None}


# ---- input robustness ---------------------------------------------------

def test_garbage_amounts_and_dates_do_not_crash_or_silently_inflate():
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0),
                      {"date": "not-a-date", "desc": "x", "amount": "abc"}],
            withdrawals=[txn(date(2026, 9, 1), "Groceries", 212.0),
                         {"date": "2026-09-02", "desc": "y", "amount": None}])["cycle"]
    assert d["paycheck"] == 2400.0
    assert d["spent"] == 212.0


def test_negative_amounts_are_read_as_magnitudes():
    """Firefly returns positive amounts with a type; a consumer that ever
    hands us signed values must not flip the arithmetic."""
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            withdrawals=[txn(date(2026, 9, 1), "Groceries", -212.0)])["cycle"]
    assert d["spent"] == 212.0


# ══ direction and single-assignment (PO_REVIEW_e7adf83 P1 findings) ═══════
#
# Two defects shipped to production in e7adf83 and were found in review.
#
# 1. `_matches` searched desc, source, destination and category TOGETHER, and
#    `_amount` takes abs(). A movement that merely NAMED a savings account was
#    therefore a savings contribution whichever way the money went — so money
#    leaving savings was reported as money entering it, and a purchase funded
#    from a savings account was removed from spending AND added to savings,
#    moving both figures the wrong way at once.
#
# 2. Every allocation rule independently scanned every transaction, so two
#    rules matching the same term each claimed the same transfer and
#    `savings_total` came out doubled — one ledger movement deducted twice
#    from what the paycheck left to spend.
#
# All fixture data here is synthetic (public repo, vision principle 6).

DIRN_MONTH = {"start": "2026-09-01", "days_elapsed": 7}
DIRN_FRESH = {"ingest_days": 0}
DIRN_TODAY = "2026-09-07"
DIRN_PAY = [{"date": "2026-09-04", "amount": 2000.0, "desc": "Synthetic paycheck",
             "source": "Employer", "destination": "Checking"}]
ONE_RULE = {"enabled": True,
            "allocations": [{"name": "Savings", "match": ["savings"], "amount": 0}]}


def _cycle(cfg, withdrawals=(), transfers=()):
    return paycheck_cycle(cfg, DIRN_TODAY, DIRN_MONTH, DIRN_PAY,
                          list(withdrawals), list(transfers), DIRN_FRESH)


def test_money_leaving_savings_is_not_a_contribution():
    """Savings -> Checking moves money OUT. It read as 300 put IN."""
    r = _cycle(ONE_RULE, transfers=[
        {"date": "2026-09-05", "amount": 300.0, "desc": "Transfer",
         "source": "Savings", "destination": "Checking"}])
    assert r["month"]["savings"] == 0.0
    assert r["cycle"]["savings_total"] == 0.0


def test_a_reverse_movement_is_reported_not_silently_dropped():
    """Not counted as its own opposite, and not made invisible either."""
    r = _cycle(ONE_RULE, transfers=[
        {"date": "2026-09-05", "amount": 300.0, "desc": "Transfer",
         "source": "Savings", "destination": "Checking"}])
    assert r["cycle"]["reverse_from_savings"] == 300.0


def test_a_purchase_funded_from_savings_is_still_spending():
    """The worst shape: `spent` fell by 250 while `savings` rose by 250."""
    r = _cycle(ONE_RULE, withdrawals=[
        {"date": "2026-09-05", "amount": 250.0, "desc": "Groceries",
         "source": "Savings", "destination": "Store"}])
    assert r["month"]["spent"] == 250.0, "a real expense vanished from spending"
    assert r["month"]["savings"] == 0.0, "an expense was counted as saving"


def test_a_genuine_contribution_still_counts():
    """The fix must not simply stop recognising savings."""
    r = _cycle(ONE_RULE, transfers=[
        {"date": "2026-09-05", "amount": 400.0, "desc": "Transfer",
         "source": "Checking", "destination": "Savings"}])
    assert r["month"]["savings"] == 400.0
    assert r["cycle"]["savings_total"] == 400.0


def test_the_description_fallback_survives():
    """A Firefly transfer is often described "Savings" while only the
    destination account says "Fidelity". That fallback is why the original
    code read the description at all, and it still has to work."""
    r = _cycle(ONE_RULE, transfers=[
        {"date": "2026-09-05", "amount": 150.0, "desc": "Savings",
         "source": "Checking", "destination": "Fidelity"}])
    assert r["cycle"]["savings_total"] == 150.0


def test_an_account_naming_savings_on_the_source_overrules_the_description():
    """Direction evidence beats a description that says otherwise."""
    r = _cycle(ONE_RULE, withdrawals=[
        {"date": "2026-09-05", "amount": 120.0, "desc": "Savings",
         "source": "Savings", "destination": "Store"}])
    assert r["month"]["spent"] == 120.0
    assert r["month"]["savings"] == 0.0


def test_a_savings_to_savings_shuffle_adds_nothing():
    """Both sides are savings: nothing moved in from the spendable pot."""
    r = _cycle(ONE_RULE, transfers=[
        {"date": "2026-09-05", "amount": 500.0, "desc": "Rebalance",
         "source": "Savings", "destination": "Savings vault"}])
    assert r["cycle"]["savings_total"] == 0.0
    assert r["cycle"]["reverse_from_savings"] == 0.0


def test_overlapping_rules_never_count_one_movement_twice():
    """Two rules, one 100 transfer. It was deducted twice."""
    cfg = {"enabled": True, "allocations": [
        {"name": "Savings", "match": ["savings"], "amount": 0},
        {"name": "Savings goal", "match": ["savings"], "amount": 0}]}
    r = _cycle(cfg, transfers=[
        {"date": "2026-09-05", "amount": 100.0, "desc": "Transfer",
         "source": "Checking", "destination": "Savings"}])
    assert r["cycle"]["savings_total"] == 100.0
    assert sum(a["amount"] for a in r["cycle"]["allocations"]) == 100.0


def test_an_ambiguous_split_is_named_rather_than_resolved_in_silence():
    """First-match-wins is a policy. A policy the reader cannot see is a
    guess presented as a reading."""
    cfg = {"enabled": True, "allocations": [
        {"name": "Savings", "match": ["savings"], "amount": 0},
        {"name": "Savings goal", "match": ["savings"], "amount": 0}]}
    r = _cycle(cfg, transfers=[
        {"date": "2026-09-05", "amount": 100.0, "desc": "Transfer",
         "source": "Checking", "destination": "Savings"}])
    winner = r["cycle"]["allocations"][0]
    assert winner["amount"] == 100.0
    assert winner["ambiguous"] == ["Savings goal"]


def test_distinct_rules_each_keep_their_own_movement():
    """Single-assignment must not starve a rule that has its own transfer."""
    cfg = {"enabled": True, "allocations": [
        {"name": "Emergency", "match": ["emergency"], "amount": 0},
        {"name": "Vacation", "match": ["vacation"], "amount": 0}]}
    r = _cycle(cfg, transfers=[
        {"date": "2026-09-05", "amount": 100.0, "desc": "T",
         "source": "Checking", "destination": "Emergency fund"},
        {"date": "2026-09-06", "amount": 60.0, "desc": "T",
         "source": "Checking", "destination": "Vacation fund"}])
    by = {a["name"]: a for a in r["cycle"]["allocations"]}
    assert by["Emergency"]["amount"] == 100.0
    assert by["Vacation"]["amount"] == 60.0
    assert r["cycle"]["savings_total"] == 160.0
    assert all(a["ambiguous"] == [] for a in r["cycle"]["allocations"])


def test_a_split_contribution_to_one_goal_is_summed_not_deduplicated():
    """Two transfers funding the same goal are 2 movements, not 1."""
    r = _cycle(ONE_RULE, transfers=[
        {"date": "2026-09-05", "amount": 100.0, "desc": "T",
         "source": "Checking", "destination": "Savings"},
        {"date": "2026-09-06", "amount": 75.0, "desc": "T",
         "source": "Checking", "destination": "Savings"}])
    assert r["cycle"]["savings_total"] == 175.0
    assert r["cycle"]["allocations"][0]["observed"] == 175.0


def test_left_to_spend_reflects_the_corrected_totals():
    """The number Anthony actually reads. Before the fix a 250 grocery run
    from savings made `left` 250 too HIGH and claimed a 250 saving."""
    r = _cycle(ONE_RULE, withdrawals=[
        {"date": "2026-09-05", "amount": 250.0, "desc": "Groceries",
         "source": "Savings", "destination": "Store"}])
    # 2000 paycheck, nothing genuinely saved, 250 genuinely spent.
    assert r["cycle"]["savings_total"] == 0.0
    assert r["cycle"]["left"] == 1750.0


# ══ a partial window supports no total (PO_REVIEW_e7adf83 finding 3) ══════
#
# The /cycle endpoint pages through Firefly under a cap. Reaching that cap
# means the ledger holds more than was read. A sum over a partial window is
# not a smaller true number — it is a wrong one, and it used to be published
# with a confident $/day figure beside it.


def _partial(**flags):
    fr = dict(DIRN_FRESH)
    fr["complete"] = {"withdrawals": True, "deposits": True, "transfers": True}
    fr["complete"].update(flags)
    fr["window_complete"] = all(fr["complete"].values())
    return fr


def _cycle_fr(cfg, fr, withdrawals=(), transfers=()):
    return paycheck_cycle(cfg, DIRN_TODAY, DIRN_MONTH, DIRN_PAY,
                          list(withdrawals), list(transfers), fr)


SOME_SPEND = [{"date": "2026-09-05", "amount": 250.0, "desc": "Groceries",
               "source": "Checking", "destination": "Store"}]


def test_a_complete_window_still_publishes_its_totals():
    """The control. Suppression must be caused by incompleteness, not by the
    completeness fields merely being present."""
    r = _cycle_fr(ONE_RULE, _partial(), withdrawals=SOME_SPEND)
    assert r["month"]["spent"] == 250.0
    assert r["cycle"]["left"] == 1750.0
    assert r["window_complete"] is True
    assert r["partial_reason"] is None


def test_a_truncated_spending_read_suppresses_spending():
    r = _cycle_fr(ONE_RULE, _partial(withdrawals=False), withdrawals=SOME_SPEND)
    assert r["month"]["spent"] is None, "a partial total was published as complete"
    assert r["month"]["daily_avg"] is None
    assert r["cycle"]["spent"] is None
    assert r["cycle"]["left"] is None


def test_a_truncated_read_suppresses_forward_guidance():
    """The $/day figure is the most confident thing on the card."""
    r = _cycle_fr(ONE_RULE, _partial(withdrawals=False), withdrawals=SOME_SPEND)
    assert r["cycle"]["per_day"] is None
    assert r["cycle"]["state"] == "unknown"


def test_a_truncated_transfer_read_suppresses_savings():
    """Transfers carry the savings contributions; a partial read understates
    them, which overstates what is left to spend."""
    r = _cycle_fr(ONE_RULE, _partial(transfers=False))
    assert r["month"]["savings"] is None
    assert r["cycle"]["left"] is None


def test_a_truncated_deposit_read_suppresses_the_cycle():
    """Worst case: the paycheck that anchors the cycle may be the row that
    was not read."""
    r = _cycle_fr(ONE_RULE, _partial(deposits=False))
    assert r["cycle"]["left"] is None
    assert r["cycle"]["state"] == "unknown"


def test_the_partial_reason_names_what_was_truncated():
    r = _cycle_fr(ONE_RULE, _partial(withdrawals=False, transfers=False))
    assert r["partial_reason"] is not None
    assert "spending" in r["partial_reason"]
    assert "transfers" in r["partial_reason"]
    assert r["window_complete"] is False


def test_incompleteness_is_reported_separately_from_staleness():
    """Stale data is old but whole; a partial read is current but incomplete.
    They need different fixes, so they must not share one message."""
    fr = _partial(withdrawals=False)
    fr["ingest_days"] = 0                     # perfectly fresh
    r = _cycle_fr(ONE_RULE, fr, withdrawals=SOME_SPEND)
    assert r["stale_reason"] is None
    assert r["partial_reason"] is not None
    assert "can't be worked out" in r["cycle"]["text"]


def test_a_missing_completeness_field_means_complete():
    """An older firefly service does not send the field. Its absence has to
    keep meaning what it meant before the field existed, or every deploy
    ordering would blank the card."""
    r = paycheck_cycle(ONE_RULE, DIRN_TODAY, DIRN_MONTH, DIRN_PAY,
                       list(SOME_SPEND), [], {"ingest_days": 0})
    assert r["month"]["spent"] == 250.0
    assert r["cycle"]["left"] == 1750.0
    assert r["window_complete"] is True
