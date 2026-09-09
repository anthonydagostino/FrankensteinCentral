"""Cash runway — PRODUCT_IDEAS #20, and the SCRUM-137 correction.

The arithmetic is one division. Everything asserted here is about which
balances go on top, which took four attempts because Firefly cannot answer it.

The load-bearing fact, verified twice from independent sources (upstream
`config/firefly.php` and the running instance on the box), is that Firefly's
COMPLETE account-role vocabulary is:

    defaultAsset, sharedAsset, savingAsset, ccAsset, cashWalletAsset

Four cash-like values and a credit card. **No role means brokerage,
investment or retirement.** A TSP and a checking account are both
`defaultAsset`, so no rule reading that field can separate them. That is a
schema limit, not an uncurated ledger, and it is why the classification lives
in configuration and the unconfigured answer is a range.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

# Loaded under an alias: every service names its package `app`, so a plain
# `from app.runway import ...` shadows budget's `app` for the whole run.
_rw = load_service_module("assistant_runway", "services/assistant/app/runway.py")
CASH_ROLES = _rw.CASH_ROLES
AMBIGUOUS_ROLES = _rw.AMBIGUOUS_ROLES
CARD_ROLE = _rw.CARD_ROLE
MAX_MONTHS = _rw.MAX_MONTHS
cash_runway = _rw.cash_runway
monthly_burn = _rw.monthly_burn
split_accounts = _rw.split_accounts

FRESH = {"window_complete": True, "ingest_days": 1}

# Firefly's real vocabulary. Pinned as a test rather than a comment because a
# value that does NOT exist sat in this module for a day looking like a working
# guard, and its tests passed by asserting on the invented string.
FIREFLY_ROLES = ("defaultAsset", "sharedAsset", "savingAsset", "ccAsset",
                 "cashWalletAsset")


def acct(name, balance, role, kind="asset"):
    return {"name": name, "balance": balance, "role": role, "kind": kind}


REAL = [
    acct("Chase Checking", 4200, "cashWalletAsset"),
    acct("Marcus Savings", 8000, "savingAsset"),
    acct("Fidelity", 90000, "defaultAsset"),
    acct("TSP", 61000, "defaultAsset"),
    acct("Amex", -1200, CARD_ROLE, kind="liability"),
]
NOT_CASH = ["Fidelity", "TSP"]


# ---- the role vocabulary is Firefly's, not ours -------------------------

def test_every_role_this_module_names_is_a_real_firefly_role():
    """`sharesAsset` lived in ILLIQUID_ROLES for a day. Firefly has never
    emitted it — the exclusion branch could not fire on real data, and the
    ledger fix being recommended to the user ("set your brokerages to
    sharesAsset") was impossible, because the option does not exist."""
    named = set(CASH_ROLES) | set(AMBIGUOUS_ROLES) | {CARD_ROLE}
    assert named <= set(FIREFLY_ROLES), (
        f"invented role(s): {named - set(FIREFLY_ROLES)}")
    assert named == set(FIREFLY_ROLES), (
        f"unhandled real role(s): {set(FIREFLY_ROLES) - named}")


def test_an_invented_role_is_never_silently_excluded():
    """If `sharesAsset` reappears it must surface as unknown, not quietly do
    the job it never did."""
    r = cash_runway([acct("Cash", 3000, "savingAsset"),
                     acct("Fidelity", 90000, "sharesAsset")],
                    1000.0, 30, freshness=FRESH)
    assert r["unknown_role"] == ["Fidelity"]
    assert "Fidelity" not in r["excluded"]


def test_a_shared_asset_account_is_cash_not_a_brokerage():
    """`sharedAsset` means a JOINT account. It is the trap that catches anyone
    who "fixes the typo" from sharesAsset — it would exclude real money."""
    r = cash_runway([acct("Joint Chequing", 5000, "sharedAsset")], 1000.0, 30,
                    freshness=FRESH, not_spendable=[])
    assert r["months"] == 5.0
    assert r["liquid_accounts"] == ["Joint Chequing"]
    assert "Joint Chequing" not in r["debts"]


def test_a_credit_card_role_is_a_debt_and_needs_no_configuration():
    """`ccAsset` is real, and Firefly enforces its shape (credit_card_type is
    required_if account_role is ccAsset). Marking a card is the one card fix
    that is performable and needs zero code."""
    r = cash_runway([acct("Cash", 3000, "savingAsset"),
                     acct("Amex", 1088.56, CARD_ROLE)],
                    1000.0, 30, freshness=FRESH)
    assert r["debts"] == ["Amex"]
    assert "Amex" not in r["liquid_accounts"]
    assert r["months_high"] == 3.0, "a card balance reached the upper bound"


# ---- the range, and what collapses it -----------------------------------

REAL_LEDGER = [
    ("Santander", 755.97, "defaultAsset"),
    ("Marcus", 20500.0, "savingAsset"),
    ("Cash wallet", 0.0, "cashWalletAsset"),
    ("Fidelity", 82000.0, "defaultAsset"),
    ("Robinhood", 20700.0, "defaultAsset"),
    ("TSP", 26600.0, "defaultAsset"),
    ("Coinbase", 210.0, "defaultAsset"),
    ("Chase Checking", 13000.0, "defaultAsset"),
    ("Platinum Card (1002)", 3016.40, "defaultAsset"),
    ("American Express Gold Card (2009)", 1088.56, "defaultAsset"),
    ("Discover (4796)", -636.68, "defaultAsset"),
]
LEDGER = [acct(n, b, r) for n, b, r in REAL_LEDGER]
REAL_BURN = 2593.11
LEDGER_NOT_CASH = ["Fidelity", "Robinhood", "TSP", "Coinbase",
                   "Platinum Card (1002)", "American Express Gold Card (2009)"]


def test_the_overstatement_is_never_published_as_a_single_number():
    """64.7 months against a true ~13.2. It may appear as the top of a stated
    range; it may never appear as the answer."""
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH)
    assert r["months"] is None, "a range was collapsed into a headline"
    assert r["certain"] is False
    assert r["months_low"] == 7.9
    assert r["months_high"] == 64.7


def test_months_is_null_rather_than_quietly_meaning_the_low_end():
    """A consumer reading `months` must never get half a range and think it
    whole. That is how 64.7 got onto the card in the first place."""
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH)
    assert r["months"] is None
    assert r["months_low"] is not None


def test_naming_the_non_cash_accounts_collapses_the_range_exactly():
    """The whole point of the configuration: it converges by declaration, not
    by a rule anyone has to get right."""
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH,
                    not_spendable=LEDGER_NOT_CASH)
    assert r["certain"] is True
    assert r["months"] == 13.2
    assert r["months_low"] == r["months_high"] == 13.2
    assert r["liquid"] == 34255.97
    assert set(r["liquid_accounts"]) == {"Marcus", "Cash wallet", "Santander",
                                         "Chase Checking"}


def test_an_empty_list_is_a_statement_and_an_absent_one_is_not():
    """Zero and unknown are different states (docs/BUDGETS.md). Having
    reviewed the accounts and excluded none is not the same as never having
    been asked, and only the second leaves the range open."""
    asked = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH, not_spendable=[])
    never = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH)
    assert asked["reviewed"] is True and asked["certain"] is True
    assert never["reviewed"] is False and never["certain"] is False


def test_the_ambiguous_accounts_are_named_so_the_width_is_actionable():
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH)
    assert "Fidelity" in r["ambiguous"] and "TSP" in r["ambiguous"]
    # Checking accounts are ambiguous too, and that is the honest reading:
    # `defaultAsset` is correct for them AND the only value a brokerage can
    # have. It is doing three jobs at once.
    assert "Santander" in r["ambiguous"] and "Chase Checking" in r["ambiguous"]


def test_the_default_role_is_not_treated_as_silence():
    """Attempt #3 counted only deliberate markings, which permanently withheld
    checking accounts — `defaultAsset` is the only correct value they can
    have — and so never converged even on a perfect ledger."""
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH,
                    not_spendable=LEDGER_NOT_CASH)
    assert "Santander" in r["liquid_accounts"]
    assert "Chase Checking" in r["liquid_accounts"]
    assert r["months"] != 7.9, "checking accounts were withheld on a fixed ledger"


def test_a_card_crossing_zero_does_not_change_what_liquid_means():
    """The cards decrement a statement-shaped opening balance, so each crosses
    zero at a different moment. If sign decided membership, the pot would
    silently change definition mid-month."""
    before = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH,
                         not_spendable=LEDGER_NOT_CASH)
    crossed = [acct(n, (-50.0 if n.startswith("Platinum") else b), r)
               for n, b, r in REAL_LEDGER]
    after = cash_runway(crossed, REAL_BURN, 30, freshness=FRESH,
                        not_spendable=LEDGER_NOT_CASH)
    assert before["liquid"] == after["liquid"] == 34255.97
    assert before["months"] == after["months"]
    assert "Platinum Card (1002)" in before["excluded"]
    assert "Platinum Card (1002)" in after["debts"]


def test_a_liability_is_never_added_to_the_pot():
    with_debt = cash_runway(REAL, 3000.0, 30, freshness=FRESH,
                            not_spendable=NOT_CASH)
    without = cash_runway([a for a in REAL if a["kind"] != "liability"],
                          3000.0, 30, freshness=FRESH, not_spendable=NOT_CASH)
    assert with_debt["liquid"] == without["liquid"] == 12200.0


@pytest.mark.parametrize("role", CASH_ROLES)
def test_every_declared_cash_role_actually_counts(role):
    """A whitelist entry that nothing can match is dead code posing as care."""
    r = cash_runway([acct("A", 3000, role)], 1000.0, 30, freshness=FRESH)
    assert r["months_low"] == 3.0


@pytest.mark.parametrize("role", AMBIGUOUS_ROLES)
def test_every_ambiguous_role_widens_the_range_rather_than_counting(role):
    r = cash_runway([acct("Cash", 3000, "savingAsset"), acct("X", 9000, role)],
                    1000.0, 30, freshness=FRESH)
    assert r["months_low"] == 3.0
    assert r["months_high"] == 12.0
    assert r["ambiguous"] == ["X"]


def test_nothing_left_to_count_is_a_reason_not_a_zero():
    r = cash_runway([acct("Amex", -500, CARD_ROLE)], 3000.0, 30, freshness=FRESH)
    assert r["months_low"] is None
    assert r["liquid"] is None
    assert "nothing to divide" in r["reason"]


# ---- every input stays visible ------------------------------------------

@pytest.mark.parametrize("cfg", [None, [], LEDGER_NOT_CASH])
@pytest.mark.parametrize("accounts", [
    LEDGER,
    REAL,
    REAL + [acct("Mystery", 5000, None), acct("Old", 1, "sharesAsset")],
    [acct("Solo", 1.0, "savingAsset")],
])
def test_every_account_is_visible_exactly_once(accounts, cfg):
    """Discover was correctly dropped for a negative balance and then appeared
    in NO published list. An input that vanishes cannot be argued with."""
    r = cash_runway(accounts, 3000.0, 30, freshness=FRESH, not_spendable=cfg)
    buckets = ("liquid_accounts", "ambiguous", "excluded", "debts")
    seen = [n for b in buckets for n in r[b]]
    assert sorted(seen) == sorted(a["name"] for a in accounts)
    assert len(seen) == len(set(seen)), "an account appeared in two buckets"


def test_the_dropped_debt_is_named_not_merely_absent():
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH)
    assert r["debts"] == ["Discover (4796)"]


# ---- when the number must not be stated --------------------------------

def test_a_truncated_read_yields_null_not_a_longer_runway():
    """A partial window understates burn, which OVERSTATES runway."""
    full = cash_runway(REAL, 3000.0, 30, freshness=FRESH, not_spendable=NOT_CASH)
    part = cash_runway(REAL, 900.0, 30, not_spendable=NOT_CASH,
                       freshness={"window_complete": False, "ingest_days": 1})
    assert full["months"] == 4.1
    assert part["months"] is None and part["months_low"] is None
    assert part["available"] is False
    assert "truncated" in part["reason"]


def test_a_stale_ledger_yields_null():
    r = cash_runway(REAL, 3000.0, 30, not_spendable=NOT_CASH,
                    freshness={"window_complete": True, "ingest_days": 9})
    assert r["months_low"] is None
    assert "9 days" in r["reason"]


def test_a_fresh_ledger_still_answers():
    for days in (0, 1, 6):
        r = cash_runway(REAL, 3000.0, 30, not_spendable=NOT_CASH,
                        freshness={"window_complete": True, "ingest_days": days})
        assert r["months"] == 4.1, days


def test_a_window_too_short_to_average_is_refused():
    assert monthly_burn(300.0, 3) is None
    r = cash_runway(REAL, 300.0, 3, freshness=FRESH, not_spendable=NOT_CASH)
    assert r["months_low"] is None
    assert "enough spending history" in r["reason"]


def test_near_zero_spend_is_not_infinite_runway():
    r = cash_runway(REAL, 0.0, 30, freshness=FRESH, not_spendable=NOT_CASH)
    assert r["months_low"] is None
    assert "essentially zero" in r["reason"]


def test_runway_is_capped_so_it_keeps_meaning_something():
    r = cash_runway([acct("Chase", 5_000_000, "savingAsset")], 30.0, 30,
                    freshness=FRESH)
    assert r["months_low"] == MAX_MONTHS


def test_no_accounts_at_all_is_answered_without_inventing_one():
    r = cash_runway([], 3000.0, 30, freshness=FRESH)
    assert r["available"] is False
    assert r["months"] is None
    assert r["liquid"] is None


# ---- the inputs stay visible -------------------------------------------

def test_every_input_is_published_so_the_number_can_be_argued_with():
    r = cash_runway(REAL, 3000.0, 30, freshness=FRESH, not_spendable=NOT_CASH)
    assert r["burn_monthly"] == 3000.0
    assert r["burn_window_days"] == 30
    assert sorted(r["liquid_accounts"]) == ["Chase Checking", "Marcus Savings"]
    assert r["liquid"] == 12200.0
    assert round(r["liquid"] / r["burn_monthly"], 1) == r["months"]


def test_the_burn_is_normalised_to_a_month_not_to_the_window():
    """A 60-day window holding $6,000 is $3,000/month, not $6,000."""
    assert monthly_burn(6000.0, 60) == 3000.0
    assert monthly_burn(1500.0, 15) == 3000.0
    r = cash_runway(REAL, 6000.0, 60, freshness=FRESH, not_spendable=NOT_CASH)
    assert r["burn_monthly"] == 3000.0
    assert r["burn_window_days"] == 60


def test_resale_income_is_separated_so_it_can_be_removed():
    r = cash_runway(REAL, 3000.0, 30, freshness=FRESH, not_spendable=NOT_CASH,
                    income={"salary": 4000.0, "resale": 900.0})
    assert r["income_resale"] == 900.0
    assert r["months_without_resale"] == round(12200.0 / 3900.0, 1)
    assert r["months_without_resale"] < r["months"]


def test_without_resale_is_not_offered_on_an_open_range():
    """A second range stacked on the first is not a readable card."""
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH,
                    income={"salary": 4000.0, "resale": 900.0})
    assert r["certain"] is False
    assert r["months_without_resale"] is None


def test_without_resale_is_absent_rather_than_equal_when_there_is_none():
    r = cash_runway(REAL, 3000.0, 30, freshness=FRESH, not_spendable=NOT_CASH,
                    income={"salary": 4000.0})
    assert r["months_without_resale"] is None


# ---- shapes ------------------------------------------------------------

def test_junk_rows_do_not_crash_or_silently_count():
    r = cash_runway([None, "nope", {}, acct("Chase", 3000, "savingAsset")],
                    1000.0, 30, freshness=FRESH)
    assert r["months_low"] == 3.0


def test_split_reports_every_bucket():
    b = split_accounts(LEDGER + [acct("Mystery", 1, None)], ["Fidelity"])
    assert [a["name"] for a in b["cash"]] == ["Marcus", "Cash wallet"]
    assert [a["name"] for a in b["excluded"]] == ["Fidelity"]
    assert [a["name"] for a in b["debts"]] == ["Discover (4796)"]
    assert [a["name"] for a in b["unknown"]] == ["Mystery"]
    assert "Chase Checking" in [a["name"] for a in b["ambiguous"]]
