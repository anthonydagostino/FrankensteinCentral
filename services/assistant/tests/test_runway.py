"""Cash runway — PRODUCT_IDEAS #20, and the SCRUM-137 correction.

The arithmetic is one division. Everything asserted here is about the ways
that division lies, all of which fail in the OPTIMISTIC direction, which is
the direction that costs money:

  * count a retirement account as spendable and thirteen months becomes sixty;
  * compute burn from a truncated read and the burn is too small, so the
    runway is too long.

Acceptance signal from the idea, verbatim: "one number, with its inputs
visible, that goes null rather than lying when the ledger hasn't been
imported."

Two earlier guards are recorded here as regression tests rather than as
history, because both shipped and both were wrong. See the module docstring.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

# Loaded under an alias: every service names its package `app`, so a plain
# `from app.runway import ...` shadows budget's `app` for the whole run.
_rw = load_service_module("assistant_runway", "services/assistant/app/runway.py")
LIQUID_ROLES = _rw.LIQUID_ROLES
ILLIQUID_ROLES = _rw.ILLIQUID_ROLES
UNSTATED_ROLE = _rw.UNSTATED_ROLE
MAX_MONTHS = _rw.MAX_MONTHS
cash_runway = _rw.cash_runway
monthly_burn = _rw.monthly_burn
split_accounts = _rw.split_accounts

FRESH = {"window_complete": True, "ingest_days": 1}


def acct(name, balance, role, kind="asset"):
    return {"name": name, "balance": balance, "role": role, "kind": kind}


# A ledger where the roles have actually been chosen. Deliberately NOT the
# shape of Anthony's real one — that is the fixture further down.
REAL = [
    acct("Chase Checking", 4200, "cashWalletAsset"),
    acct("Marcus Savings", 8000, "savingAsset"),
    acct("Fidelity", 90000, "sharesAsset"),
    acct("TSP", 61000, "sharesAsset"),
    acct("Amex", -1200, "ccAsset", kind="liability"),
]


# ---- what counts as spendable ------------------------------------------

def test_retirement_is_not_grocery_money():
    """The failure the whole feature turns on: $12,200 of cash against a
    $3,000 burn is four months. Counting the brokerages makes it fifty."""
    r = cash_runway(REAL, 3000.0, 30, freshness=FRESH)
    assert r["liquid"] == 12200.0
    assert r["months"] == 4.1
    assert set(r["excluded"]) == {"Fidelity", "TSP"}


def test_a_liability_is_never_added_to_the_pot():
    with_debt = cash_runway(REAL, 3000.0, 30, freshness=FRESH)
    without = cash_runway([a for a in REAL if a["kind"] != "liability"],
                          3000.0, 30, freshness=FRESH)
    assert with_debt["liquid"] == without["liquid"]


def test_a_role_we_have_never_heard_of_is_unclassified_not_liquid():
    r = cash_runway([acct("Crypto", 9999, "someNewRoleFireflyAdded")], 500.0, 30,
                    freshness=FRESH)
    assert r["available"] is False
    assert r["unclassified"] == ["Crypto"]


def test_an_account_with_no_role_at_all_is_unclassified_not_liquid():
    r = cash_runway(REAL + [acct("Mystery", 5000, None)], 3000.0, 30,
                    freshness=FRESH)
    assert r["liquid"] == 12200.0, "an unroled account was counted as cash"
    assert r["lower_bound"] is True
    assert r["unclassified"] == ["Mystery"]
    assert r["months"] == 4.1


@pytest.mark.parametrize("role", LIQUID_ROLES)
def test_every_declared_liquid_role_actually_counts(role):
    """A whitelist entry that nothing can match is dead code posing as care."""
    r = cash_runway([acct("A", 3000, role)], 1000.0, 30, freshness=FRESH)
    assert r["liquid"] == 3000.0
    assert r["months"] == 3.0


@pytest.mark.parametrize("role", ILLIQUID_ROLES)
def test_every_declared_illiquid_role_actually_excludes(role):
    r = cash_runway([acct("Cash", 3000, "savingAsset"), acct("X", 90000, role)],
                    1000.0, 30, freshness=FRESH)
    assert r["liquid"] == 3000.0
    assert r["excluded"] == ["X"]


def test_no_cash_account_is_a_reason_not_a_zero():
    r = cash_runway([acct("Fidelity", 90000, "sharesAsset")], 3000.0, 30,
                    freshness=FRESH)
    assert r["months"] is None
    assert r["liquid"] is None
    assert "nothing to divide" in r["reason"]


# ---- SCRUM-137: `defaultAsset` is silence, not a claim -------------------
#
# Two guards shipped before this one and both failed. The first trusted
# `account_role` outright and defended only against a role that was MISSING.
# The second suppressed when every asset role was IDENTICAL, and was written
# against a docstring claim — "Anthony's Firefly reports defaultAsset for all
# eleven accounts" — that was never checked and is false. The ledger carries
# three distinct roles, so that guard could never fire, and the card went on
# publishing 64.7 months against a true ~13.2.
#
# The fixture below is the live ledger, read off the box via raw Firefly API
# output rather than summarised: name, balance, account_role.

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


def test_the_real_ledger_no_longer_publishes_the_overstatement():
    """The regression, with the exact data that produced it. 64.7 came from
    dividing brokerages, a TSP and two credit cards by the burn."""
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH)
    assert r["months"] != 64.7
    assert r["months"] != 64.5
    assert r["liquid"] != 167870.93
    # Only what the ledger deliberately marked.
    assert r["liquid_accounts"] == ["Marcus", "Cash wallet"]
    assert r["liquid"] == 20500.0
    assert r["months"] == 7.9


def test_the_real_ledger_figure_is_published_as_a_floor():
    """7.9 against a true ~13.2. Understating is the safe direction, but only
    if the card SAYS it is understating."""
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH)
    assert r["lower_bound"] is True
    assert r["months"] < 13.2


def test_no_credit_card_reaches_the_numerator():
    """All three cards are entered as asset accounts with a debt-shaped
    opening balance. None may be counted as cash."""
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH)
    for card in ("Platinum Card (1002)", "American Express Gold Card (2009)",
                 "Discover (4796)"):
        assert card not in r["liquid_accounts"]


def test_a_card_crossing_zero_does_not_change_what_liquid_means():
    """The defect the negative-balance rule masked: those cards decrement a
    statement-shaped opening balance, so each crosses zero at a different
    moment. If sign decided membership, `liquid` would silently change
    definition mid-month. Role does, so the balance can move freely."""
    before = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH)
    crossed = [acct(n, (-50.0 if n.startswith("Platinum") else b), r)
               for n, b, r in REAL_LEDGER]
    after = cash_runway(crossed, REAL_BURN, 30, freshness=FRESH)
    assert before["liquid"] == after["liquid"] == 20500.0
    assert before["months"] == after["months"]
    # It moves between visible buckets, and is never hidden by the move.
    assert "Platinum Card (1002)" in before["unstated"]
    assert "Platinum Card (1002)" in after["debts"]


def test_the_default_role_is_treated_as_silence_not_as_cash():
    """Nine accounts sit at Firefly's unset value — two checking accounts, two
    brokerages, a TSP, a crypto account and three cards. Reading that as an
    assertion that they are all spendable is the entire bug."""
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH)
    for name in ("Santander", "Chase Checking", "Fidelity", "TSP", "Robinhood",
                 "Coinbase"):
        assert name in r["unstated"]
        assert name not in r["liquid_accounts"]


def test_variety_in_the_role_field_does_not_make_it_trustworthy():
    """The guard that shipped and could not fire. This ledger HAS three
    distinct roles; the uniformity check saw variety and concluded health.
    Nine accounts agreeing on a wrong answer is not a signal of doubt."""
    roles = {a["role"] for a in LEDGER}
    assert len(roles) == 3, "fixture no longer reproduces the SCRUM-137 ledger"
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH)
    assert r["months"] == 7.9, "variety was again mistaken for correctness"


def test_a_ledger_with_roles_actually_set_gives_the_true_figure():
    """The fix must not merely suppress everything: once the roles say what
    the accounts are, the same balances give the number the ledger supports.
    This is what Anthony's five-minute Firefly edit buys."""
    fixed = [acct("Chase Checking", 13000.0, "cashWalletAsset"),
             acct("Santander", 755.97, "cashWalletAsset"),
             acct("Marcus", 20500.0, "savingAsset"),
             acct("Cash wallet", 0.0, "cashWalletAsset"),
             acct("Fidelity", 82000.0, "sharesAsset"),
             acct("TSP", 26600.0, "sharesAsset"),
             acct("Robinhood", 20700.0, "sharesAsset"),
             acct("Coinbase", 210.0, "sharesAsset"),
             acct("Platinum Card (1002)", -3016.40, "ccAsset", kind="liability"),
             acct("Amex Gold (2009)", -1088.56, "ccAsset", kind="liability"),
             acct("Discover (4796)", -636.68, "ccAsset", kind="liability")]
    r = cash_runway(fixed, REAL_BURN, 30, freshness=FRESH)
    assert r["liquid"] == 34255.97
    assert r["months"] == 13.2
    assert r["lower_bound"] is False, "nothing is withheld once roles are set"


def test_a_ledger_entirely_at_the_default_role_is_suppressed_not_guessed():
    """Nothing deliberately marked as cash means there is no pot. A zero here
    would be a lie and a number would be an invention."""
    allsilent = [acct(n, b, "defaultAsset") for n, b, _ in REAL_LEDGER]
    r = cash_runway(allsilent, REAL_BURN, 30, freshness=FRESH)
    assert r["available"] is False
    assert r["months"] is None
    assert r["liquid"] is None


def test_the_suppression_names_the_ledger_fix_not_a_bug():
    """A reason the reader can act on. This is a Firefly data fix, and the
    text has to say so or it reads as the dashboard being broken."""
    allsilent = [acct(n, b, "defaultAsset") for n, b, _ in REAL_LEDGER]
    r = cash_runway(allsilent, REAL_BURN, 30, freshness=FRESH)
    assert "default role" in r["reason"]
    assert "Santander" in r["reason"], "the reader needs to know which accounts"


# ---- every input stays visible ------------------------------------------

@pytest.mark.parametrize("accounts", [
    LEDGER,
    REAL,
    REAL + [acct("Mystery", 5000, None)],
    [acct(n, b, "defaultAsset") for n, b, _ in REAL_LEDGER],
    [acct("Solo", 1.0, "savingAsset")],
])
def test_every_account_is_visible_exactly_once(accounts):
    """Discover was correctly dropped for a negative balance and then appeared
    in NO published list — neither `excluded` nor `unclassified`. An input that
    vanishes cannot be argued with, and the card's whole claim to trust is that
    its inputs are visible. Exactly one bucket, never zero, never two."""
    r = cash_runway(accounts, 3000.0, 30, freshness=FRESH)
    buckets = ("liquid_accounts", "unstated", "unclassified", "excluded",
               "debts")
    seen = [n for b in buckets for n in r[b]]
    assert sorted(seen) == sorted(a["name"] for a in accounts)
    assert len(seen) == len(set(seen)), "an account appeared in two buckets"


def test_the_dropped_debt_is_named_not_merely_absent():
    r = cash_runway(LEDGER, REAL_BURN, 30, freshness=FRESH)
    assert r["debts"] == ["Discover (4796)"]


# ---- when the number must not be stated --------------------------------

def test_a_truncated_read_yields_null_not_a_longer_runway():
    """The acceptance signal. A partial window understates burn, which
    OVERSTATES runway — the optimistic direction."""
    full = cash_runway(REAL, 3000.0, 30, freshness=FRESH)
    part = cash_runway(REAL, 900.0, 30,
                       freshness={"window_complete": False, "ingest_days": 1})
    assert full["months"] == 4.1
    assert part["months"] is None
    assert part["available"] is False
    assert "truncated" in part["reason"]
    # and specifically not the flattering number the partial read would give
    assert part["months"] != 13.6


def test_a_stale_ledger_yields_null():
    """Spending that hasn't been imported hasn't stopped happening."""
    r = cash_runway(REAL, 3000.0, 30,
                    freshness={"window_complete": True, "ingest_days": 9})
    assert r["months"] is None
    assert "9 days" in r["reason"]


def test_a_fresh_ledger_still_answers():
    for days in (0, 1, 6):
        r = cash_runway(REAL, 3000.0, 30,
                        freshness={"window_complete": True, "ingest_days": days})
        assert r["months"] == 4.1, days


def test_a_window_too_short_to_average_is_refused():
    assert monthly_burn(300.0, 3) is None
    r = cash_runway(REAL, 300.0, 3, freshness=FRESH)
    assert r["months"] is None
    assert "enough spending history" in r["reason"]


def test_near_zero_spend_is_not_infinite_runway():
    r = cash_runway(REAL, 0.0, 30, freshness=FRESH)
    assert r["months"] is None
    assert "essentially zero" in r["reason"]


def test_runway_is_capped_so_it_keeps_meaning_something():
    r = cash_runway([acct("Chase", 5_000_000, "savingAsset")], 30.0, 30,
                    freshness=FRESH)
    assert r["months"] == MAX_MONTHS


def test_no_accounts_at_all_is_answered_without_inventing_one():
    r = cash_runway([], 3000.0, 30, freshness=FRESH)
    assert r["available"] is False
    assert r["months"] is None
    assert r["liquid"] is None


# ---- the inputs stay visible -------------------------------------------

def test_every_input_is_published_so_the_number_can_be_argued_with():
    r = cash_runway(REAL, 3000.0, 30, freshness=FRESH)
    assert r["burn_monthly"] == 3000.0
    assert r["burn_window_days"] == 30
    assert r["liquid_accounts"] == ["Chase Checking", "Marcus Savings"]
    assert r["liquid"] == 12200.0
    assert round(r["liquid"] / r["burn_monthly"], 1) == r["months"]


def test_the_burn_is_normalised_to_a_month_not_to_the_window():
    """A 60-day window holding $6,000 is $3,000/month, not $6,000."""
    assert monthly_burn(6000.0, 60) == 3000.0
    assert monthly_burn(1500.0, 15) == 3000.0
    r = cash_runway(REAL, 6000.0, 60, freshness=FRESH)
    assert r["burn_monthly"] == 3000.0
    assert r["burn_window_days"] == 60


def test_resale_income_is_separated_so_it_can_be_removed():
    """"How long do I last" and "how long do I last if resale stops" are
    different questions; resale is real but lumpy and self-directed."""
    r = cash_runway(REAL, 3000.0, 30, freshness=FRESH,
                    income={"salary": 4000.0, "resale": 900.0})
    assert r["income_resale"] == 900.0
    assert r["months_without_resale"] == round(12200.0 / 3900.0, 1)
    assert r["months_without_resale"] < r["months"]


def test_without_resale_is_absent_rather_than_equal_when_there_is_none():
    r = cash_runway(REAL, 3000.0, 30, freshness=FRESH, income={"salary": 4000.0})
    assert r["months_without_resale"] is None


# ---- shapes ------------------------------------------------------------

def test_junk_rows_do_not_crash_or_silently_count():
    r = cash_runway([None, "nope", {}, acct("Chase", 3000, "savingAsset")],
                    1000.0, 30, freshness=FRESH)
    assert r["liquid"] == 3000.0


def test_split_reports_all_five_buckets():
    b = split_accounts(LEDGER + [acct("Mystery", 1, None),
                                 acct("Fund", 10, "sharesAsset")])
    assert [a["name"] for a in b["liquid"]] == ["Marcus", "Cash wallet"]
    assert [a["name"] for a in b["illiquid"]] == ["Fund"]
    assert [a["name"] for a in b["debts"]] == ["Discover (4796)"]
    assert [a["name"] for a in b["unclassified"]] == ["Mystery"]
    assert "Fidelity" in [a["name"] for a in b["unstated"]]
