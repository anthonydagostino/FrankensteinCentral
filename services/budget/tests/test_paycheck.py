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
        ingest_days=0, month_ingested=True, ledger_latest=None,
        window_complete=True):
    # `window_complete` is stated rather than omitted: silence means "the
    # ledger did not say", which suppresses every paycheck-derived figure. A
    # fixture exercising the ordinary case has to say the window was whole,
    # the same way the real payload does.
    return paycheck_cycle(
        cfg=CFG if cfg is None else cfg,
        today=today, month=month_of(today),
        deposits=list(deposits), withdrawals=list(withdrawals),
        transfers=list(transfers),
        freshness={"ingest_days": ingest_days, "activity_days": ingest_days,
                   "month_ingested": month_ingested,
                   "window_complete": window_complete,
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


# ---- direction: which way the money actually moved -----------------------
#
# Matching an account name anywhere in the row cannot tell a contribution from
# its exact opposite. All three rows below mention "Fidelity" and only the
# first is saving. Codex reproduced the first two against production e7adf83;
# the third is the same defect seen from the spending side.

def test_money_coming_back_out_of_savings_is_not_a_contribution():
    """`Fidelity -> Checking` is the opposite of saving.

    Counted as a contribution it inflates savings and understates what is
    left, which is the shape Codex reproduced: a $300 reverse transfer read as
    $300 saved.
    """
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            transfers=[txn(date(2026, 8, 29), "Transfer", 300.0,
                           source="Fidelity Brokerage", destination="Checking")],
            cfg={**CFG, "allocations": [{"name": "Fidelity", "amount": 0,
                                         "match": ["fidelity"]}]})["cycle"]
    allocs = {a["name"]: a for a in d["allocations"]}
    assert allocs["Fidelity"]["observed"] is None, "a withdrawal FROM savings was booked as a contribution"
    assert d["savings_total"] == 0.0
    assert d["left"] == 2400.0


def test_a_purchase_funded_from_savings_is_spending():
    """`Fidelity -> Coffee Shop` is a purchase, not a deposit.

    The sharper half of the same bug: the row is removed from `spent` AND
    added to savings, so $45 that left the account renders as a confident
    $0.00 spent. docs/BUDGETS.md exists to stop exactly that.
    """
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            withdrawals=[txn(date(2026, 9, 2), "Coffee shop", 45.0, "Dining",
                             source="Fidelity Brokerage", destination="Coffee Shop")],
            cfg={**CFG, "allocations": [{"name": "Fidelity", "amount": 0,
                                         "match": ["fidelity"]}]})
    assert d["cycle"]["spent"] == 45.0, "a real purchase vanished from spending"
    assert d["month"]["spent"] == 45.0


def test_the_destination_still_identifies_savings_the_description_does_not_name():
    """The reason direction is not simply "read the description".

    A Firefly transfer is routinely described "Savings" while only the
    destination account says "Fidelity". Narrowing to the destination must not
    break that, and narrowing to the description would.
    """
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            transfers=[txn(date(2026, 8, 29), "Savings", 1100.0,
                           source="Checking", destination="Fidelity Brokerage")])["cycle"]
    allocs = {a["name"]: a for a in d["allocations"]}
    assert allocs["Fidelity"]["source"] == "observed"
    assert allocs["Fidelity"]["observed"] == 1100.0


# ---- one movement, one rule ---------------------------------------------

def test_two_rules_matching_the_same_movement_deduct_it_once():
    """Every rule used to scan every transaction independently, so one $100
    transfer was debited twice when two rules both matched "savings"."""
    cfg = {**CFG, "allocations": [
        {"name": "Savings", "amount": 100, "match": ["savings"]},
        {"name": "Savings Pot", "amount": 100, "match": ["savings"]}]}
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            transfers=[txn(date(2026, 8, 29), "To savings", 100.0,
                           source="Checking", destination="Savings")],
            cfg=cfg)["cycle"]
    assert d["savings_total"] == 100.0, "the same $100 was deducted under two rules"
    assert d["left"] == 2300.0
    allocs = {a["name"]: a for a in d["allocations"]}
    assert allocs["Savings"]["source"] == "observed"
    # The loser reports why it has nothing, rather than falling back to its
    # configured amount — which would re-create the double deduction as
    # "expected" and leave it unexplainable on screen.
    assert allocs["Savings Pot"]["source"] == "ambiguous_rule"
    assert allocs["Savings Pot"]["amount"] == 0.0
    assert allocs["Savings Pot"]["claimed_by"] == ["Savings"]


def test_a_split_transfer_still_counts_every_part():
    """The guard against over-correcting. Two genuinely separate $50 transfers
    on the same day are identical dicts; deduplicating by content rather than
    by position would silently halve them."""
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            transfers=[txn(date(2026, 8, 29), "To savings", 50.0,
                           source="Checking", destination="Marcus Savings"),
                       txn(date(2026, 8, 29), "To savings", 50.0,
                           source="Checking", destination="Marcus Savings")],
            cfg={**CFG, "allocations": [{"name": "Marcus", "amount": 500,
                                         "match": ["marcus"]}]})["cycle"]
    allocs = {a["name"]: a for a in d["allocations"]}
    assert allocs["Marcus"]["observed"] == 100.0, "one half of a split transfer was dropped"


def test_an_unmatched_rule_still_reports_its_expected_amount():
    """`ambiguous_rule` must fire only on a genuine collision. A rule whose
    movement simply has not been imported yet is still `expected`, unchanged."""
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            transfers=[txn(date(2026, 8, 29), "Savings", 1100.0,
                           destination="Fidelity Brokerage")])["cycle"]
    allocs = {a["name"]: a for a in d["allocations"]}
    assert allocs["Marcus"]["source"] == "expected"
    assert allocs["Marcus"]["amount"] == 500.0
    assert allocs["Marcus"]["claimed_by"] is None


# ---- a partial window is never presented as a complete one ---------------


# ---- a withheld rule is not a claim on the ledger ------------------------

def test_a_pre_deposit_rule_does_not_swallow_a_real_transfer():
    """Codex's reproduction of the first fix's own bug.

    `already_withheld` means the EMPLOYER removed the money before the deposit
    landed, so a transfer sitting in the ledger after payday is by definition
    not that money. The rule used to claim the row anyway, contribute 0 as
    withheld, and leave the genuine post-deposit rule reporting
    `ambiguous_rule` — so a real $100 transfer vanished from savings entirely
    and `left` came back as the whole paycheck.
    """
    cfg = {**CFG, "allocations": [
        {"name": "401k", "amount": 300, "match": ["savings"], "already_withheld": True},
        {"name": "Savings", "amount": 100, "match": ["savings"]}]}
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            transfers=[txn(date(2026, 8, 29), "To savings", 100.0,
                           source="Checking", destination="Savings")],
            cfg=cfg)["cycle"]
    allocs = {a["name"]: a for a in d["allocations"]}
    assert allocs["Savings"]["source"] == "observed", "a withheld rule consumed the transfer"
    assert allocs["Savings"]["amount"] == 100.0
    assert allocs["401k"]["source"] == "withheld_before_deposit"
    assert allocs["401k"]["amount"] == 0.0
    assert d["savings_total"] == 100.0
    assert d["left"] == 2300.0


def test_a_withheld_rule_alone_consumes_nothing():
    """The same rule with no competitor. It still contributes 0, and the
    transfer is still not counted as saved by it."""
    cfg = {**CFG, "allocations": [
        {"name": "401k", "amount": 300, "match": ["savings"], "already_withheld": True}]}
    d = run(date(2026, 9, 4),
            deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
            transfers=[txn(date(2026, 8, 29), "To savings", 100.0,
                           source="Checking", destination="Savings")],
            cfg=cfg)["cycle"]
    assert d["savings_total"] == 0.0
    assert d["allocations"][0]["claimed_by"] is None


# ---- a window we did not read in full establishes almost nothing ---------

def _partial(window_complete, **over):
    kw = {"ingest_days": 0, "activity_days": 0, "month_ingested": True,
          "ledger_latest_txn": "2026-09-04"}
    if window_complete is not _MISSING:
        kw["window_complete"] = window_complete
    kw.update(over)
    return paycheck_cycle(
        cfg=CFG, today=date(2026, 9, 4), month=month_of(date(2026, 9, 4)),
        deposits=[txn(date(2026, 8, 28), "ACME PAYROLL", 2400.0)],
        withdrawals=[txn(date(2026, 9, 1), "Groceries", 212.0, "Groceries")],
        transfers=[], freshness=kw)


_MISSING = object()


def test_a_truncated_window_suppresses_every_figure_it_cannot_support():
    """`spent` is the only survivor, and only as a lower bound.

    An earlier draft called all the totals "a floor". That was wrong in the
    dangerous direction: missing WITHDRAWALS make `spent` too low and so
    `left` too HIGH — a truncated window reporting more money available than
    there is. Missing deposits push it the other way, and missing history can
    put the cycle boundary somewhere else entirely.
    """
    d = _partial(False)
    c = d["cycle"]
    assert c["spent"] == 212.0, "observed spending really did happen"
    assert c["spent_is_lower_bound"] is True
    for unsupported in ("paycheck", "spendable", "left", "per_day"):
        assert c[unsupported] is None, f"{unsupported} was claimed from a partial window"
    assert c["state"] == "unknown"
    assert d["month"]["daily_avg"] is None
    assert d["month"]["spent_is_lower_bound"] is True
    assert "isn't established" in c["text"]
    assert "at least" in c["text"].lower()


def test_no_completeness_evidence_is_unknown_not_complete():
    """A payload that does not carry the flag has not said the window was
    whole. Reading silence as "complete" is how a silent truncation gets
    rendered as a confident number."""
    d = _partial(_MISSING)
    assert d["window_complete"] is None
    assert d["cycle"]["left"] is None
    assert d["cycle"]["spent"] == 212.0


def test_a_complete_window_still_answers_in_full():
    """The guard against over-suppression: nothing above may make the ordinary
    case go quiet."""
    d = _partial(True)
    c = d["cycle"]
    assert d["window_complete"] is True
    assert c["paycheck"] == 2400.0
    assert c["spendable"] == 800.0
    assert c["left"] == 588.0
    assert c["per_day"] is not None
    assert c["spent_is_lower_bound"] is False
    assert d["stale_reason"] is None
