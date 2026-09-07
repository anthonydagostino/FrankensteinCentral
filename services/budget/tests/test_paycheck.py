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


# ---- Codex PO review of e7adf83: two P1 calculation defects --------------
# Both shipped to production and produced confident, wrong dollar figures.
# Each test below was confirmed to fail against the code as deployed.

def test_a_transfer_OUT_of_savings_is_not_a_contribution():
    """P1. Matching source and destination indiscriminately turned a $300
    withdrawal FROM savings into a $300 contribution TO it: savings_total=300,
    left=1700 on a $2,000 paycheck. Direction is the whole point."""
    cfg = {**CFG, "allocations": [{"name": "Savings", "amount": 0,
                                   "match": ["savings"]}]}
    d = run(date(2026, 9, 4), cfg=cfg,
            deposits=[txn(date(2026, 8, 28), "PAYROLL", 2000.0)],
            transfers=[txn(date(2026, 8, 29), "Transfer", 300.0,
                           source="Savings", destination="Checking")])["cycle"]
    assert d["savings_total"] == 0.0
    assert d["left"] == 2000.0
    assert d["from_savings"] == 300.0     # surfaced, never silently dropped


def test_a_purchase_paid_from_the_savings_account_is_still_spending():
    """P1, same root cause: a real $150 grocery run funded from savings
    vanished from both cycle spend and month spend."""
    cfg = {**CFG, "allocations": [{"name": "Savings", "amount": 0,
                                   "match": ["savings"]}]}
    d = run(date(2026, 9, 4), cfg=cfg,
            deposits=[txn(date(2026, 8, 28), "PAYROLL", 2000.0)],
            withdrawals=[txn(date(2026, 9, 1), "Costco", 150.0, "Groceries",
                             source="Savings")])
    assert d["cycle"]["spent"] == 150.0
    assert d["month"]["spent"] == 150.0


def test_a_contribution_still_reads_as_savings_when_the_destination_matches():
    """The fix must not break the case it protects: money INTO savings."""
    cfg = {**CFG, "allocations": [{"name": "Savings", "amount": 0,
                                   "match": ["savings"]}]}
    d = run(date(2026, 9, 4), cfg=cfg,
            deposits=[txn(date(2026, 8, 28), "PAYROLL", 2000.0)],
            transfers=[txn(date(2026, 8, 29), "Transfer", 300.0,
                           source="Checking", destination="Savings")])["cycle"]
    assert d["savings_total"] == 300.0
    assert d["left"] == 1700.0


def test_overlapping_allocations_deduct_one_movement_once():
    """P1. Two rules both matching "savings" each scanned every transaction,
    so one $100 transfer was deducted twice (savings_total=200, left=1800)."""
    cfg = {**CFG, "allocations": [
        {"name": "Savings A", "amount": 0, "match": ["savings"]},
        {"name": "Savings B", "amount": 0, "match": ["savings"]}]}
    d = run(date(2026, 9, 4), cfg=cfg,
            deposits=[txn(date(2026, 8, 28), "PAYROLL", 2000.0)],
            transfers=[txn(date(2026, 8, 29), "Savings", 100.0,
                           source="Checking", destination="Savings")])["cycle"]
    assert d["savings_total"] == 100.0
    assert d["left"] == 1900.0
    # first rule wins, and the ambiguous config is named rather than hidden
    assert d["allocation_overlaps"]
    assert sum(a["amount"] for a in d["allocations"]) == 100.0


def test_split_transactions_still_sum_within_one_allocation():
    """Single-assignment must not collapse genuinely separate movements."""
    cfg = {**CFG, "allocations": [{"name": "Fidelity", "amount": 0,
                                   "match": ["fidelity"]}]}
    d = run(date(2026, 9, 4), cfg=cfg,
            deposits=[txn(date(2026, 8, 28), "PAYROLL", 2000.0)],
            transfers=[txn(date(2026, 8, 29), "a", 600.0, destination="Fidelity"),
                       txn(date(2026, 8, 30), "b", 500.0, destination="Fidelity")])["cycle"]
    assert d["savings_total"] == 1100.0
    assert not d["allocation_overlaps"]


# ---- P2: a truncated fetch window is not a total ------------------------

def test_a_truncated_window_suppresses_totals_and_guidance():
    """P2. The 75-day window caps paging; the helper used to return its
    partial list with no completeness signal, so a long ledger undercounted
    spending while still publishing daily guidance."""
    d = paycheck_cycle(
        cfg=CFG, today=date(2026, 9, 4), month=month_of(date(2026, 9, 4)),
        deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
        withdrawals=[txn(date(2026, 9, 1), "Groceries", 212.0)],
        transfers=[],
        freshness={"ingest_days": 0, "activity_days": 0, "month_ingested": True,
                   "ledger_latest_txn": "2026-09-04", "window_complete": False})
    assert d["window_complete"] is False
    assert d["month"]["spent"] is None          # unknown, not a partial total
    assert d["month"]["daily_avg"] is None
    assert d["cycle"]["per_day"] is None        # no guidance off a partial window
    assert "partial view" in d["stale_reason"]


def test_a_complete_window_is_unaffected():
    d = standard()
    assert d["window_complete"] is True
    assert d["month"]["complete"] is True
    assert d["cycle"]["per_day"] is not None


# ---- peer review of 2acceff (Protocol Agent) ----------------------------
# Two gaps in the P2 fix, both reproduced against 2acceff before fixing.

def test_a_truncated_window_suppresses_left_not_just_the_month():
    """`left` is paycheck − savings − spent, so a truncated withdrawal read
    biases it UPWARD: 2acceff suppressed the month block and per_day but still
    published "$1,750 left to spend" from a window it had recorded as
    partial, in the calm `ok` style."""
    cfg = {**CFG, "allocations": [{"name": "Savings", "amount": 0,
                                   "match": ["savings"]}]}
    d = paycheck_cycle(
        cfg=cfg, today=date(2026, 9, 7),
        month=month_of(date(2026, 9, 7)),
        deposits=[txn(date(2026, 9, 4), "ACME PAYROLL", 2000.0)],
        withdrawals=[txn(date(2026, 9, 5), "Groceries", 250.0,
                         source="Checking", destination="Store")],
        transfers=[],
        freshness={"ingest_days": 0, "activity_days": 0, "month_ingested": True,
                   "ledger_latest_txn": "2026-09-07", "window_complete": False})
    c = d["cycle"]
    assert c["left"] is None            # the number that must not survive
    assert c["spent"] is None
    assert c["per_day"] is None
    assert c["state"] == "unknown"      # not the calm style
    assert "partial read would overstate" in c["text"]


def test_a_complete_window_still_publishes_left():
    """The control: suppression must be caused by incompleteness, nothing else."""
    cfg = {**CFG, "allocations": [{"name": "Savings", "amount": 0,
                                   "match": ["savings"]}]}
    c = paycheck_cycle(
        cfg=cfg, today=date(2026, 9, 7), month=month_of(date(2026, 9, 7)),
        deposits=[txn(date(2026, 9, 4), "ACME PAYROLL", 2000.0)],
        withdrawals=[txn(date(2026, 9, 5), "Groceries", 250.0,
                         source="Checking", destination="Store")],
        transfers=[],
        freshness={"ingest_days": 0, "activity_days": 0, "month_ingested": True,
                   "ledger_latest_txn": "2026-09-07"})["cycle"]
    assert c["spent"] == 250.0
    assert c["left"] == 1750.0
    assert c["state"] == "ok"


def test_savings_by_description_alone_is_flagged_not_dropped():
    """A transfer described "Savings" whose accounts are Checking -> Fidelity
    matches the rule by description only. Direction is unknowable from a
    description, so it is counted neither way — but it must not vanish
    silently, because dropping it understates savings and pushes `left` up."""
    cfg = {**CFG, "allocations": [{"name": "Savings", "amount": 0,
                                   "match": ["savings"]}]}
    c = run(date(2026, 9, 7), cfg=cfg,
            deposits=[txn(date(2026, 9, 4), "ACME PAYROLL", 2000.0)],
            transfers=[txn(date(2026, 9, 5), "Savings", 150.0,
                           source="Checking", destination="Fidelity")])["cycle"]
    assert c["savings_total"] == 0.0            # not guessed at
    assert len(c["unmatched_savings"]) == 1     # and not silent
    u = c["unmatched_savings"][0]
    assert u["amount"] == 150.0 and u["rule"] == "Savings"


def test_a_description_never_overrules_the_accounts():
    """The reason the description cannot decide: "Savings withdrawal" moving
    Fidelity -> Checking is money coming OUT. Reading direction from the
    description would call it a contribution."""
    assert pc._savings_role(
        {"desc": "Savings withdrawal", "source": "Fidelity",
         "destination": "Checking", "category": ""}, ["savings"]) is None
    # while the accounts, when they do carry the term, decide unambiguously
    assert pc._savings_role(
        {"desc": "x", "source": "Checking", "destination": "Savings",
         "category": ""}, ["savings"]) == "contribution"
    assert pc._savings_role(
        {"desc": "x", "source": "Savings", "destination": "Checking",
         "category": ""}, ["savings"]) == "reverse"


def test_account_matched_savings_is_not_flagged_as_unmatched():
    """A rule that matches the account name works normally and raises nothing."""
    cfg = {**CFG, "allocations": [{"name": "Fidelity", "amount": 0,
                                   "match": ["fidelity"]}]}
    c = run(date(2026, 9, 7), cfg=cfg,
            deposits=[txn(date(2026, 9, 4), "ACME PAYROLL", 2000.0)],
            transfers=[txn(date(2026, 9, 5), "Savings", 150.0,
                           source="Checking", destination="Fidelity")])["cycle"]
    assert c["savings_total"] == 150.0
    assert c["unmatched_savings"] == []


# ---- Codex FC-009 review -------------------------------------------------

def test_a_withheld_rule_never_consumes_a_real_movement():
    """FC-009 finding 1. Single-assignment took first-match-wins without
    consulting already_withheld, so a pre-deposit rule claimed a real $100
    post-deposit transfer and then deducted zero by design — the contribution
    vanished and `left` read $100 high. Confirmed present in both candidates."""
    cfg = {**CFG, "allocations": [
        {"name": "401k", "match": ["savings"], "already_withheld": True},
        {"name": "Savings", "match": ["savings"]}]}
    c = run(date(2026, 9, 7), cfg=cfg,
            deposits=[txn(date(2026, 9, 4), "PAYROLL", 2000.0)],
            transfers=[txn(date(2026, 9, 5), "x", 100.0,
                           source="Checking", destination="Savings")])["cycle"]
    assert c["savings_total"] == 100.0
    assert c["left"] == 1900.0
    allocs = {a["name"]: a for a in c["allocations"]}
    assert allocs["Savings"]["amount"] == 100.0      # the rule that can account for it
    assert allocs["401k"]["amount"] == 0.0           # still deducts nothing, correctly
    assert c["withheld_rule_conflicts"] == []


def test_a_movement_matching_only_a_withheld_rule_is_flagged():
    """The control: when the ONLY matching rule is pre-deposit, the movement
    still cannot be deducted — but that means the configuration is wrong, so
    it is named rather than silently zeroed."""
    cfg = {**CFG, "allocations": [
        {"name": "401k", "match": ["savings"], "already_withheld": True}]}
    c = run(date(2026, 9, 7), cfg=cfg,
            deposits=[txn(date(2026, 9, 4), "PAYROLL", 2000.0)],
            transfers=[txn(date(2026, 9, 5), "x", 100.0,
                           source="Checking", destination="Savings")])["cycle"]
    assert c["savings_total"] == 0.0
    assert len(c["withheld_rule_conflicts"]) == 1
    assert c["withheld_rule_conflicts"][0]["amount"] == 100.0
    assert c["withheld_rule_conflicts"][0]["rule"] == "401k"


def test_a_truncated_window_makes_every_money_figure_unknown():
    """FC-009 finding 2, second half. Suppressing `left` alone was not enough:
    a truncated DEPOSIT read makes the paycheck itself wrong, so `paycheck`
    and `spendable` were exact-looking numbers from an unknown window. The
    error runs both ways — missing withdrawals overstate `left`, a missing
    deposit understates it — so none of these is a floor."""
    cfg = {**CFG, "allocations": [{"name": "Savings", "amount": 0,
                                   "match": ["savings"]}]}
    c = paycheck_cycle(
        cfg=cfg, today=date(2026, 9, 7), month=month_of(date(2026, 9, 7)),
        deposits=[txn(date(2026, 9, 4), "PAYROLL", 2000.0)],
        withdrawals=[txn(date(2026, 9, 5), "Groceries", 250.0,
                         source="Checking", destination="Store")],
        transfers=[],
        freshness={"ingest_days": 0, "activity_days": 0, "month_ingested": True,
                   "ledger_latest_txn": "2026-09-07", "window_complete": False})["cycle"]
    assert c["figures_complete"] is False
    for key in ("paycheck", "spendable", "spent", "left",
                "savings_total", "from_savings", "per_day"):
        assert c[key] is None, f"{key} survived a truncated window"
    assert c["state"] == "unknown"


# ══ the counter-case: a description cannot establish direction ═══════════
#
# This one row broke three separate implementations of the direction fix,
# mine included, so it is pinned here rather than left as a review anecdote.
#
#     desc="Savings withdrawal"  source="Fidelity"  destination="Checking"
#
# Every guard that consulted the description as a fallback booked this as a
# CONTRIBUTION, because the source account ("Fidelity") does not contain the
# matched term ("savings") — so the direction guard never fired and the rule
# fell through to the description, which does contain it.
#
# The word appears identically on both legs of a transfer. Only the accounts
# carry direction. A row whose accounts do not name the savings target is an
# unknown, and an unknown reported as a contribution overstates savings and
# overstates what is left to spend — the exact error the direction work
# existed to remove, pointed the other way.

DIR_MONTH = {"start": "2026-09-01", "days_elapsed": 7, "label": "September 2026",
             "days_total": 30, "days_left": 23}
DIR_FRESH = {"ingest_days": 0, "window_complete": True,
             "complete": {"withdrawals": True, "deposits": True, "transfers": True}}
DIR_PAY = [txn("2026-09-04", "Payroll", 2000.0,
               source="Employer", destination="Checking")]
DIR_CFG = {"enabled": True, "match": ["payroll"], "min_amount": 500,
           "allocations": [{"name": "Savings", "match": ["savings"], "amount": 0}]}


def _dir_cycle(withdrawals=(), transfers=()):
    return paycheck_cycle(DIR_CFG, "2026-09-07", DIR_MONTH, DIR_PAY,
                          list(withdrawals), list(transfers), DIR_FRESH)["cycle"]


def test_a_description_never_makes_an_outflow_a_contribution():
    """Money leaving Fidelity, described "Savings withdrawal"."""
    c = _dir_cycle(transfers=[txn("2026-09-05", "Savings withdrawal", 150.0,
                                  source="Fidelity", destination="Checking")])
    assert c["savings_total"] == 0.0, \
        "the description set the direction; money leaving savings was booked into it"
    assert c["left"] == 2000.0, "a phantom contribution reduced what is left to spend"


def test_the_counter_case_is_wrong_on_a_complete_window_too():
    """It is not rescued by completeness suppression. With a complete window
    nothing blanks the figure, so the wrong number renders in the calm state
    with a confident $/day beside it."""
    c = _dir_cycle(transfers=[txn("2026-09-05", "Savings withdrawal", 150.0,
                                  source="Fidelity", destination="Checking")])
    assert c["state"] == "ok", "precondition: this window is complete and unsuppressed"
    assert "$2,000" in c["text"], c["text"]


def test_the_three_rows_that_all_say_savings_are_told_apart():
    """The truth table the whole direction fix rests on."""
    into = _dir_cycle(transfers=[txn("2026-09-05", "Transfer", 400.0,
                                     source="Checking", destination="Savings")])
    assert into["savings_total"] == 400.0, "a real contribution stopped counting"

    out_of = _dir_cycle(transfers=[txn("2026-09-05", "Transfer", 300.0,
                                       source="Savings", destination="Checking")])
    assert out_of["savings_total"] == 0.0

    purchase = _dir_cycle(withdrawals=[txn("2026-09-05", "Groceries", 250.0,
                                           source="Savings", destination="Store")])
    assert purchase["savings_total"] == 0.0
    assert purchase["left"] == 1750.0, "a purchase funded from savings vanished from spending"
