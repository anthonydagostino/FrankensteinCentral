"""Cash runway — pure, deterministic, no I/O.

PRODUCT_IDEAS #20: every other money figure on this dashboard looks backwards —
what you spent, what's due, what a category has left. Runway is the only one
that looks forward, and it is the one that changes a decision: whether to take
the contract, how hard to push on offers, whether a purchase waits a month.

    liquid balances ÷ trailing average monthly burn = months of runway

The arithmetic is trivial. Everything below is about the two ways it lies.

**It lies if "liquid" is wrong.** Dividing total net worth by burn counts a
retirement account as grocery money and turns four months into forty. So only
Firefly's cash-like roles count (`defaultAsset`, `savingAsset`, `cashWalletAsset`).
A brokerage is excluded, a liability is excluded, and an account whose role
Firefly never set is neither counted nor silently dropped: it is reported in
`unclassified`, and while any exists the runway is a LOWER BOUND, because money
we refused to count can only make it longer.

**It lies if the burn is wrong.** A burn computed from a truncated read is too
small, which makes runway too long — the optimistic direction, which is the
dangerous one. So a partial window yields None, never a number. Same for a
stale ledger: spending that hasn't been imported hasn't stopped happening.

Income is split salary from resale so the picture can be read both ways: resale
is real income but it is lumpy and self-directed, and "how long do I last if
resale stops" is a different question from "how long do I last".
"""
from __future__ import annotations

# Firefly's account_role values that are spendable today. Deliberately a
# whitelist: a role this list has not heard of is unclassified, not liquid.
LIQUID_ROLES = ("defaultAsset", "savingAsset", "cashWalletAsset")
# Roles that are real money but not reachable this month without a decision.
ILLIQUID_ROLES = ("sharesAsset", "ccAsset")

MIN_BURN = 1.0          # below this, months-of-runway is a divide-by-noise
MAX_MONTHS = 120        # past ten years the number stops meaning anything


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def split_accounts(accounts) -> dict:
    """Sort accounts into what you can spend, what you can't, and what we
    can't tell. The third bucket is the one that must never be guessed."""
    liquid, illiquid, unclassified, liabilities = [], [], [], []
    for a in accounts or []:
        if not isinstance(a, dict):
            continue
        row = {"name": a.get("name") or "", "balance": _num(a.get("balance")),
               "role": a.get("role")}
        if a.get("kind") == "liability":
            liabilities.append(row)
        elif row["role"] in LIQUID_ROLES:
            liquid.append(row)
        elif row["role"] in ILLIQUID_ROLES:
            illiquid.append(row)
        else:
            unclassified.append(row)
    return {"liquid": liquid, "illiquid": illiquid,
            "unclassified": unclassified, "liabilities": liabilities}


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


def cash_runway(accounts, spend_total, window_days, *, freshness=None,
                income=None) -> dict:
    """Months of runway, or an honest None with the reason why.

    `freshness` carries `window_complete` (see docs/BUDGETS.md) and
    `ingest_days`; either can suppress the figure. `income` optionally splits
    trailing income into salary and resale so runway can be read with and
    without the lumpy half.
    """
    fr = freshness or {}
    buckets = split_accounts(accounts)
    liquid_total = round(sum(a["balance"] for a in buckets["liquid"]), 2)
    burn = monthly_burn(spend_total, window_days)

    reason = None
    if fr.get("window_complete", True) is False:
        # A truncated read UNDERSTATES spending, so it OVERSTATES runway. Of the
        # two directions to be wrong in, that is the one that costs you money.
        reason = "the spending read was truncated, so the burn would be too low"
    elif not buckets["liquid"]:
        reason = ("no account is marked as cash in Firefly, so there is nothing "
                  "to divide")
    elif burn is None:
        reason = "not enough spending history to average a month"
    elif burn < MIN_BURN:
        reason = "trailing spend is essentially zero, so runway isn't meaningful"

    ingest_days = fr.get("ingest_days")
    if reason is None and isinstance(ingest_days, (int, float)) and ingest_days >= 7:
        reason = (f"nothing has been imported for {int(ingest_days)} days, so the "
                  f"burn is out of date")

    months = None
    if reason is None:
        months = round(min(liquid_total / burn, MAX_MONTHS), 1)

    inc = income or {}
    salary = inc.get("salary")
    resale = inc.get("resale")
    # Runway if the resale income stopped: the same liquid pot against a burn
    # that resale is no longer offsetting. Only stated when both are known.
    without_resale = None
    if months is not None and isinstance(resale, (int, float)) and resale > 0:
        without_resale = round(min(liquid_total / (burn + resale), MAX_MONTHS), 1)

    return {
        "available": months is not None,
        "months": months,
        "reason": reason,
        # Every input visible, so the number can be argued with.
        "liquid": liquid_total if buckets["liquid"] else None,
        "liquid_accounts": [a["name"] for a in buckets["liquid"]],
        "burn_monthly": burn,
        "burn_window_days": int(_num(window_days, 0)) or None,
        # True => money exists that we refused to classify, so `months` is a
        # floor. Counting it would only lengthen the runway, never shorten it.
        "lower_bound": bool(buckets["unclassified"]),
        "unclassified": [a["name"] for a in buckets["unclassified"]],
        "excluded": [a["name"] for a in buckets["illiquid"]],
        "income_salary": salary,
        "income_resale": resale,
        "months_without_resale": without_resale,
    }
