"""Accounts, cash, and the buy that lands every other Monday.

The portfolio was one flat list of holdings. Anthony, 2026-10-07: show the
Fidelity accounts alongside Robinhood, one total for the two Fidelity accounts,
exactly right, and "take into account what gets deposited every two weeks and
to where". So: accounts with their own cash, and a recurring plan the service
applies on its due date.

Everything here is pure and date-injected, so the application rule is swept
across a calendar rather than tested on the day it was written. Fixture
figures are synthetic — the repository is public.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

st = load_service_module("stocks_main_accounts", "services/stocks/app/main.py")

PRICES = {"VOO": 500.0, "QQQ": 400.0, "VTI": 250.0, "VXUS": 60.0}
QUOTES = {s: {"price": p, "change": 1.0, "change_pct": 0.2, "source": "test"}
          for s, p in PRICES.items()}
QUOTES["NVDA"] = {"price": 100.0, "change": -2.0, "change_pct": -1.96, "source": "test"}


def accounts():
    return [
        {"name": "Robinhood", "holdings": [{"symbol": "NVDA", "shares": 10, "cost": 80}], "cash": 0},
        {"name": "Fidelity Roth", "holdings": [{"symbol": "VOO", "shares": 2, "cost": 400}], "cash": 10.5},
        {"name": "Fidelity Individual", "holdings": [{"symbol": "VOO", "shares": 4, "cost": 450}], "cash": 1.0},
    ]


def plan(next_date, account="Fidelity Individual"):
    return [{"account": account, "cadence_days": 14, "next": next_date,
             "buys": {"VOO": 500, "QQQ": 230, "VTI": 220, "VXUS": 150}}]


# ---- reading settings of either vintage ------------------------------------

def test_the_flat_holdings_list_is_read_as_the_robinhood_account():
    got = st.accounts_from({"holdings": [{"symbol": "NVDA", "shares": 3}]})
    assert [a["name"] for a in got] == ["Robinhood"]
    assert got[0]["holdings"][0]["symbol"] == "NVDA" and got[0]["cash"] == 0.0


def test_accounts_win_over_the_flat_list_once_they_exist():
    got = st.accounts_from({"holdings": [{"symbol": "OLD", "shares": 1}],
                            "accounts": [{"name": "A", "holdings": [], "cash": "5"}]})
    assert [a["name"] for a in got] == ["A"] and got[0]["cash"] == 5.0


@pytest.mark.parametrize("market", [{}, None, {"accounts": [], "holdings": []},
                                    {"accounts": [{"holdings": []}]}])
def test_nothing_configured_is_an_empty_list(market):
    assert st.accounts_from(market) == []


# ---- the recurring buy ---------------------------------------------------------

def test_a_plan_not_yet_due_changes_nothing():
    acc, plans, applied = st.apply_due_contributions(accounts(), plan("2026-10-12"), PRICES,
                                                     date(2026, 10, 7))
    assert applied == []
    assert plans[0]["next"] == "2026-10-12"
    assert acc[2]["holdings"] == accounts()[2]["holdings"]


def test_on_the_day_the_buy_becomes_estimated_shares_at_the_days_price():
    acc, plans, applied = st.apply_due_contributions(accounts(), plan("2026-10-12"), PRICES,
                                                     date(2026, 10, 12))
    ind = acc[2]
    by = {h["symbol"]: h for h in ind["holdings"]}
    assert by["VOO"]["shares"] == 5.0                 # 4 + 500/500
    assert by["QQQ"]["shares"] == 0.575               # 230/400
    assert by["VTI"]["shares"] == 0.88 and by["VXUS"]["shares"] == 2.5
    assert by["VOO"]["estimated_shares"] == 1.0 and by["QQQ"]["estimated_shares"] == 0.575
    assert by["VOO"]["estimated_since"] == "2026-10-12"
    assert plans[0]["next"] == "2026-10-26"
    assert applied[0]["amount"] == 1100.0 and applied[0]["account"] == "Fidelity Individual"
    # The other accounts are untouched: the money went WHERE the plan says.
    assert acc[1]["holdings"] == accounts()[1]["holdings"]
    assert acc[0]["holdings"] == accounts()[0]["holdings"]


def test_cost_basis_blends_rather_than_resets():
    """4 shares at $450 plus $500 of new money: total gain must stay true."""
    acc, _, _ = st.apply_due_contributions(accounts(), plan("2026-10-12"), PRICES,
                                           date(2026, 10, 12))
    voo = next(h for h in acc[2]["holdings"] if h["symbol"] == "VOO")
    assert voo["cost"] == pytest.approx((450 * 4 + 500) / 5)


def test_every_missed_period_is_applied_and_the_plan_catches_up():
    """A box that was off for five weeks still owes the ledger three buys."""
    acc, plans, applied = st.apply_due_contributions(accounts(), plan("2026-10-12"), PRICES,
                                                     date(2026, 11, 10))
    assert [a["on"] for a in applied] == ["2026-10-12", "2026-10-26", "2026-11-09"]
    assert all(a["priced_on"] == "2026-11-10" for a in applied)
    assert plans[0]["next"] == "2026-11-23"
    voo = next(h for h in acc[2]["holdings"] if h["symbol"] == "VOO")
    assert voo["shares"] == 7.0


def test_a_missing_quote_defers_the_whole_basket_rather_than_guessing():
    prices = {k: v for k, v in PRICES.items() if k != "VXUS"}
    acc, plans, applied = st.apply_due_contributions(accounts(), plan("2026-10-12"), prices,
                                                     date(2026, 10, 12))
    assert applied == []
    assert plans[0]["next"] == "2026-10-12", "an unapplied plan must not advance"
    assert acc[2]["holdings"] == accounts()[2]["holdings"]


def test_an_unknown_account_is_not_invented():
    acc, plans, applied = st.apply_due_contributions(accounts(), plan("2026-10-12", "Nope"),
                                                     PRICES, date(2026, 10, 12))
    assert applied == [] and len(acc) == 3


@pytest.mark.parametrize("bad", [{"next": "garbage"}, {"next": None}, {"cadence_days": 0}])
def test_a_malformed_plan_is_skipped_not_crashed(bad):
    p = {**plan("2026-10-12")[0], **bad}
    acc, plans, applied = st.apply_due_contributions(accounts(), [p], PRICES, date(2026, 10, 12))
    assert applied == []


def test_the_inputs_are_not_mutated():
    before = accounts()
    st.apply_due_contributions(before, plan("2026-10-12"), PRICES, date(2026, 10, 12))
    assert before == accounts()


@pytest.mark.parametrize("today", [date(2026, 1, 1) + timedelta(days=i) for i in range(0, 731, 3)],
                         ids=lambda d: d.isoformat())
def test_across_a_calendar_next_always_lands_after_today_on_the_cadence(today):
    """The sweep: whatever day it runs, `next` ends up in the future and on
    the fortnightly grid anchored to the original date."""
    _, plans, applied = st.apply_due_contributions(accounts(), plan("2026-01-05"), PRICES, today)
    nxt = date.fromisoformat(plans[0]["next"])
    assert nxt > today
    assert (nxt - date(2026, 1, 5)).days % 14 == 0
    assert len(applied) == max(0, (today - date(2026, 1, 5)).days // 14 + 1)


# ---- the next buy, for the card ---------------------------------------------

def test_next_contribution_names_the_account_date_and_basket():
    n = st.next_contribution(plan("2026-10-12"), date(2026, 10, 7))
    assert n["account"] == "Fidelity Individual" and n["days_until"] == 5
    assert n["amount"] == 1100.0 and n["buys"]["VOO"] == 500.0


def test_no_plan_is_none():
    assert st.next_contribution([], date(2026, 10, 7)) is None


# ---- valuing accounts ---------------------------------------------------------

def test_each_account_is_valued_with_its_cash_and_the_total_is_their_sum():
    out = st.price_accounts(accounts(), QUOTES)
    by = {a["name"]: a for a in out["accounts"]}
    assert by["Fidelity Roth"]["value"] == 2 * 500 + 10.5
    assert by["Fidelity Individual"]["value"] == 4 * 500 + 1.0
    assert by["Robinhood"]["value"] == 1000.0
    assert out["value"] == pytest.approx(1000 + 1010.5 + 2001.0)
    assert by["Fidelity Roth"]["total_gain"] == 200.0
    assert by["Robinhood"]["total_gain"] == 200.0


def test_positions_are_combined_by_symbol_across_accounts():
    out = st.price_accounts(accounts(), QUOTES)
    voo = next(p for p in out["positions"] if p["symbol"] == "VOO")
    assert voo["shares"] == 6.0 and voo["value"] == 3000.0
    assert sorted(voo["accounts"]) == ["Fidelity Individual", "Fidelity Roth"]
    assert voo["total_gain"] == 200.0 + 200.0


def test_an_unpriced_symbol_is_listed_not_dropped():
    out = st.price_accounts([{"name": "A", "holdings": [{"symbol": "ZZZZ", "shares": 1}], "cash": 0}], {})
    assert out["quotes_failed"] == ["ZZZZ"]
    assert out["accounts"][0]["positions"][0]["available"] is False
    assert out["value"] == 0.0


def test_estimated_lots_are_reported_per_account():
    acc, _, _ = st.apply_due_contributions(accounts(), plan("2026-10-12"), PRICES, date(2026, 10, 12))
    out = st.price_accounts(acc, QUOTES)
    ind = next(a for a in out["accounts"] if a["name"] == "Fidelity Individual")
    assert sorted(ind["estimated"]["symbols"]) == ["QQQ", "VOO", "VTI", "VXUS"]
    assert ind["estimated"]["since"] == "2026-10-12"
    roth = next(a for a in out["accounts"] if a["name"] == "Fidelity Roth")
    assert roth["estimated"] == {"symbols": [], "since": None}
