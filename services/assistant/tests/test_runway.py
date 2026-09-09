"""Cash runway — PRODUCT_IDEAS #20.

The arithmetic is one division. Everything asserted here is about the two ways
that division lies, both of which fail in the OPTIMISTIC direction, which is
the direction that costs money:

  * count a retirement account as spendable and four months becomes forty;
  * compute burn from a truncated read and the burn is too small, so the
    runway is too long.

Acceptance signal from the idea, verbatim: "one number, with its inputs
visible, that goes null rather than lying when the ledger hasn't been
imported."
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
MAX_MONTHS = _rw.MAX_MONTHS
cash_runway = _rw.cash_runway
monthly_burn = _rw.monthly_burn
split_accounts = _rw.split_accounts

FRESH = {"window_complete": True, "ingest_days": 1}


def acct(name, balance, role, kind="asset"):
    return {"name": name, "balance": balance, "role": role, "kind": kind}


REAL = [
    acct("Chase Checking", 4200, "defaultAsset"),
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


def test_an_unroled_account_is_neither_counted_nor_dropped():
    """Firefly does not always set a role. Counting it would inflate runway;
    silently ignoring it would hide real money. So it is excluded AND the
    figure is flagged a lower bound."""
    r = cash_runway(REAL + [acct("Mystery", 5000, None)], 3000.0, 30, freshness=FRESH)
    assert r["liquid"] == 12200.0, "an unroled account was counted as cash"
    assert r["lower_bound"] is True
    assert r["unclassified"] == ["Mystery"]
    assert r["months"] == 4.1


def test_a_role_we_have_never_heard_of_is_unclassified_not_liquid():
    r = cash_runway([acct("Crypto", 9999, "someNewRoleFirefly Added")], 500.0, 30,
                    freshness=FRESH)
    assert r["available"] is False
    assert r["unclassified"] == ["Crypto"]


@pytest.mark.parametrize("role", LIQUID_ROLES)
def test_every_declared_liquid_role_actually_counts(role):
    """A whitelist entry that nothing can match is dead code posing as care."""
    r = cash_runway([acct("A", 3000, role)], 1000.0, 30, freshness=FRESH)
    assert r["liquid"] == 3000.0
    assert r["months"] == 3.0


def test_no_cash_account_is_a_reason_not_a_zero():
    r = cash_runway([acct("Fidelity", 90000, "sharesAsset")], 3000.0, 30, freshness=FRESH)
    assert r["months"] is None
    assert r["liquid"] is None
    assert "nothing to divide" in r["reason"]


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
    r = cash_runway([acct("Chase", 5_000_000, "defaultAsset")], 30.0, 30, freshness=FRESH)
    assert r["months"] == MAX_MONTHS


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
    r = cash_runway([None, "nope", {}, acct("Chase", 3000, "defaultAsset")],
                    1000.0, 30, freshness=FRESH)
    assert r["liquid"] == 3000.0
    assert r["months"] == 3.0


def test_no_accounts_at_all_is_answered_without_inventing_one():
    r = cash_runway([], 3000.0, 30, freshness=FRESH)
    assert r["available"] is False
    assert r["months"] is None
    assert r["liquid"] is None


def test_split_reports_all_four_buckets():
    b = split_accounts(REAL + [acct("Mystery", 1, None)])
    assert [a["name"] for a in b["liquid"]] == ["Chase Checking", "Marcus Savings"]
    assert [a["name"] for a in b["illiquid"]] == ["Fidelity", "TSP"]
    assert [a["name"] for a in b["liabilities"]] == ["Amex"]
    assert [a["name"] for a in b["unclassified"]] == ["Mystery"]
