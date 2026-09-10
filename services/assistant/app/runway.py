"""Cash runway — pure, deterministic, no I/O.

PRODUCT_IDEAS #20: every other money figure on this dashboard looks backwards —
what you spent, what's due, what a category has left. Runway is the only one
that looks forward, and it is the one that changes a decision: whether to take
the contract, how hard to push on offers, whether a purchase waits a month.

    liquid balances ÷ trailing average monthly burn = months of runway

The arithmetic is one division. Everything below is about which balances go on
top, which is a question Firefly cannot fully answer — and the four attempts it
took to work that out.

## Why this cannot be inferred from `account_role`

Firefly III's complete role vocabulary, from `config/firefly.php` upstream:

    defaultAsset, sharedAsset, savingAsset, ccAsset, cashWalletAsset

**There is no role meaning brokerage, investment or retirement.** Firefly does
not model investment accounts. So a TSP and a checking account are both
`defaultAsset`, and no rule reading that field can ever tell them apart — not
because the ledger is uncurated, but because the vocabulary has no word for the
distinction. `sharedAsset` is a *joint* account, which is cash; it is not, and
has never been, "shares".

That last point cost a day. This module previously listed `sharesAsset` as an
illiquid role. **No such value exists in Firefly.** The "brokerages are
excluded" branch had never fired on real data, and the tests only proved it
fired on a string this module invented. Worse, the ledger fix being recommended
to the user — "set your investments to sharesAsset" — was impossible to
perform: the option is not in the dropdown, because the concept is not in
Firefly.

## Three rules that shipped or nearly shipped, and why each failed

  1. **Trust the role.** Defends against a role that is MISSING. The real
     ledger's roles were present and wrong, so nothing ever fired and the card
     published **64.7 months** against a true ~13.2 — a 4.9x overstatement,
     unhedged, in the direction the comments here call expensive.
  2. **Suppress when every role is identical.** Written against a docstring
     claim, stated as fact and never checked, that the ledger reported
     `defaultAsset` for all eleven accounts. It reports three distinct roles,
     so the guard could never fire. *Variety is not correctness*: nine accounts
     agreeing on a wrong answer is not a signal of doubt.
  3. **Treat `defaultAsset` as silence and count only deliberate markings.**
     Never converges. `defaultAsset` is the only correct value a plain checking
     account can have, so even on a perfectly curated ledger this permanently
     withholds the current accounts, under-reports by ~40%, and tells the user
     to go fix two accounts that are already correct. A rule whose behaviour on
     perfect data is still wrong is the wrong rule, however safe its direction.

Each was an attempt to infer, from the data, a fact the data does not contain.
A fourth would fail the same way.

## What this module does instead

**Uncertainty is represented, not resolved.** Where Firefly genuinely cannot
say whether an account is spendable, the answer is a RANGE, and the width of
that range is the honest measure of how much the ledger has not been asked:

  * `savingAsset` / `cashWalletAsset` — a deliberate "this is cash". Counted.
  * `ccAsset`, a liability, or a negative balance — a debt. Never counted.
  * `defaultAsset` / `sharedAsset` — **ambiguous**: correct for a checking
    account AND the only available value for a brokerage. Counted in the upper
    bound only.

A range needs no gate, and every gate proposed for this problem had a hole.
It also converges by construction rather than by a rule someone has to get
right: when nothing is ambiguous, low and high are equal and the card shows a
single number.

**The ambiguity is resolved by configuration, because it cannot be resolved by
inference.** `not_spendable` names the accounts that are not part of the cash
pot. Listing them is a statement that the accounts have been reviewed, so
everything else that is not a debt is cash, and the range collapses. This is a
fix the user can actually perform, unlike the Firefly edit that was previously
recommended. Re-entering credit cards as Firefly *liabilities* remains a real
fix too, and is handled without configuration.

## Two findings kept because they are expensive to rediscover

**The obvious behavioural rule is inverted here.** "Count the accounts spending
actually flows out of" sounds like behaviour beating metadata. Over 60 days of
the real ledger, all 112 withdrawals have a CREDIT CARD as their source and
every cash account has zero — the user spends on cards. That rule would select
exactly the three cards as the numerator and exclude every real account.

**A negative balance is a debt backstop, not a card detector.** The cards were
entered as assets whose opening balance is a statement figure, and charges
decrement it like a cash pot, so each crosses zero at a different moment. A
rule that admits an account until its spending happens to exceed its opening
balance is a definition that moves silently as the month goes on.

**It also lies if the burn is wrong.** A burn computed from a truncated read is
too small, which makes runway too long — the optimistic direction. So a partial
window yields None, never a number. Same for a stale ledger: spending that
hasn't been imported hasn't stopped happening.

**Every account lands in exactly one visible bucket.** An input that vanishes
cannot be argued with, and being argued with is the point.
"""
from __future__ import annotations

# Firefly's complete asset-role vocabulary. Verified against config/firefly.php
# upstream, NOT from memory — a value invented here once sat in this module for
# a day looking like a working guard.
#
# A deliberate statement that this money is spendable now.
CASH_ROLES = ("savingAsset", "cashWalletAsset")
# Correct for a checking account; ALSO the only value available for a
# brokerage, because Firefly has no role for one. Hence ambiguous, and hence
# the upper bound rather than the lower.
AMBIGUOUS_ROLES = ("defaultAsset", "sharedAsset")
# The one role that says "not cash". Firefly offers nothing for investments.
CARD_ROLE = "ccAsset"

MIN_BURN = 1.0          # below this, months-of-runway is a divide-by-noise
MAX_MONTHS = 120        # past ten years the number stops meaning anything


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def split_accounts(accounts, not_spendable=None) -> dict:
    """Sort accounts into cash, debt, explicitly-excluded, and can't-tell.

    `not_spendable` names accounts the operator has declared out of the pot.
    Supplying it at all is the statement that the accounts have been reviewed,
    which is what lets the remaining ambiguity collapse — see `cash_runway`.
    """
    named = {n for n in (not_spendable or ()) if n}
    cash, ambiguous, excluded, debts, unknown = [], [], [], [], []
    for a in accounts or []:
        if not isinstance(a, dict):
            continue
        row = {"name": a.get("name") or "", "balance": _num(a.get("balance")),
               "role": a.get("role")}
        if (a.get("kind") == "liability" or row["role"] == CARD_ROLE
                or row["balance"] < 0):
            # Firefly says debt, or the role says credit card, or it is an
            # "asset" carrying a debt. A backstop, not a classifier.
            debts.append(row)
        elif row["name"] in named:
            excluded.append(row)
        elif row["role"] in CASH_ROLES:
            cash.append(row)
        elif row["role"] in AMBIGUOUS_ROLES:
            ambiguous.append(row)
        else:
            # A role no version of this module has heard of, or none at all.
            # Treated as ambiguous for the arithmetic but reported separately,
            # because it means Firefly emitted something unexpected and that is
            # worth knowing on its own.
            unknown.append(row)
    return {"cash": cash, "ambiguous": ambiguous, "excluded": excluded,
            "debts": debts, "unknown": unknown}


def monthly_burn(spend_total, window_days) -> float | None:
    """Trailing spend normalised to a month. None when the window is unusable —
    a burn from three days of data is not a monthly burn."""
    days = _num(window_days, 0)
    if days < 14 or spend_total is None:
        return None
    total = _num(spend_total, -1)
    if total < 0:
        return None
    return round(total * (30.0 / days), 2)


def _months(total, burn) -> float:
    return round(min(total / burn, MAX_MONTHS), 1)


def cash_runway(accounts, spend_total, window_days, *, freshness=None,
                income=None, not_spendable=None) -> dict:
    """Months of runway as a range, or an honest None with the reason why.

    `freshness` carries `window_complete` (see docs/BUDGETS.md) and
    `ingest_days`; either suppresses the figure entirely. `income` optionally
    splits trailing income into salary and resale. `not_spendable` is the
    operator's list of accounts that are not part of the cash pot; see the
    module docstring for why this cannot be inferred.
    """
    fr = freshness or {}
    reviewed = not_spendable is not None
    buckets = split_accounts(accounts, not_spendable)
    # Supplying the exclusion list is the statement that the accounts have been
    # looked at. Everything left that is not a debt is then cash, and the range
    # collapses to a point. Without it, Firefly cannot distinguish a brokerage
    # from a checking account and neither can this module.
    unresolved = [] if reviewed else buckets["ambiguous"] + buckets["unknown"]
    counted = buckets["cash"] + ([] if not reviewed else
                                 buckets["ambiguous"] + buckets["unknown"])

    low_total = round(sum(a["balance"] for a in counted), 2)
    high_total = round(low_total + sum(a["balance"] for a in unresolved), 2)
    burn = monthly_burn(spend_total, window_days)

    reason = None
    if fr.get("window_complete", True) is False:
        # A truncated read UNDERSTATES spending, so it OVERSTATES runway. Of the
        # two directions to be wrong in, that is the one that costs you money.
        reason = "the spending read was truncated, so the burn would be too low"
    elif not counted and not unresolved:
        reason = ("no account is left to count as cash, so there is nothing "
                  "to divide")
    elif burn is None:
        reason = "not enough spending history to average a month"
    elif burn < MIN_BURN:
        reason = "trailing spend is essentially zero, so runway isn't meaningful"

    ingest_days = fr.get("ingest_days")
    if reason is None and isinstance(ingest_days, (int, float)) and ingest_days >= 7:
        reason = (f"nothing has been imported for {int(ingest_days)} days, so the "
                  f"burn is out of date")

    months_low = months_high = None
    if reason is None:
        months_low, months_high = _months(low_total, burn), _months(high_total, burn)

    # A single figure only when there is genuinely one. `months` stays null
    # while the range is open rather than quietly meaning the low end — a
    # consumer reading `months` must never get half a range and think it whole.
    certain = months_low is not None and months_low == months_high
    months = months_low if certain else None

    inc = income or {}
    salary, resale = inc.get("salary"), inc.get("resale")
    # Runway if the resale income stopped: the same pot against a burn that
    # resale is no longer offsetting. Only stated when the figure is certain,
    # because a second range on top of the first is not a readable card.
    without_resale = None
    if certain and isinstance(resale, (int, float)) and resale > 0:
        without_resale = _months(low_total, burn + resale)

    return {
        "available": months_low is not None,
        # Null while the range is open. Read `months_low`/`months_high` then.
        "months": months,
        "months_low": months_low,
        "months_high": months_high,
        "certain": certain,
        "reason": reason,
        # Every input visible, so the number can be argued with.
        "liquid": low_total if months_low is not None else None,
        "liquid_high": high_total if months_low is not None else None,
        "liquid_accounts": [a["name"] for a in counted],
        "burn_monthly": burn,
        "burn_window_days": int(_num(window_days, 0)) or None,
        # Firefly cannot say whether these are spendable. Naming them is what
        # makes the width of the range actionable rather than mysterious.
        "ambiguous": [a["name"] for a in unresolved],
        # Firefly emitted a role this module does not know. Distinct from
        # ambiguous-by-design: this one means go and look.
        "unknown_role": [a["name"] for a in buckets["unknown"]],
        # Declared out of the pot by configuration.
        "excluded": [a["name"] for a in buckets["excluded"]],
        # Debts and "assets" carrying a debt. Present so an account that is
        # dropped is still SEEN to be dropped.
        "debts": [a["name"] for a in buckets["debts"]],
        # False => no exclusion list is configured, so the range is open
        # because nobody has said which accounts are cash — not because the
        # ledger is broken.
        "reviewed": reviewed,
        "income_salary": salary,
        "income_resale": resale,
        "months_without_resale": without_resale,
    }
