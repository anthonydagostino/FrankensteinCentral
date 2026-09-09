"""Cash runway — pure, deterministic, no I/O.

PRODUCT_IDEAS #20: every other money figure on this dashboard looks backwards —
what you spent, what's due, what a category has left. Runway is the only one
that looks forward, and it is the one that changes a decision: whether to take
the contract, how hard to push on offers, whether a purchase waits a month.

    liquid balances ÷ trailing average monthly burn = months of runway

The arithmetic is trivial. Everything below is about the ways it lies.

**It lies if "liquid" is wrong.** Dividing total net worth by burn counts a
retirement account as grocery money and turns thirteen months into sixty-five.
That is not hypothetical: it shipped, and it is the reason this module now
looks the way it does.

Two guards have been tried against it. The history matters more than the code,
because both failed in the same way and the third attempt has to not.

**Attempt one — trust `account_role`.** Only Firefly's cash-like roles counted
(`defaultAsset`, `savingAsset`, `cashWalletAsset`); a role Firefly never set
made the account `unclassified` and the figure a lower bound. This defends
against a role that is MISSING.

**Attempt two — distrust a uniform `account_role`.** If every asset account
carried the same role, that was read as a default the ledger never filled in,
and everything was suppressed. This defends against a role that is UNIFORM.

**Both were wrong, and wrong for the same reason.** Each was written against an
assumption about Anthony's ledger that was never checked. Attempt two's
docstring asserted, as established fact, "Anthony's Firefly reports
`defaultAsset` for all eleven accounts." It does not. It reports three distinct
roles — `defaultAsset` x9, `savingAsset` (Marcus), `cashWalletAsset` (Cash
wallet) — so the uniformity check could never fire, and the card went on
publishing **64.7 months** against a true figure near **13.2**. A 4.9x
overstatement, unhedged, in the direction this module's own comments call the
expensive one.

The lesson, recorded here because it is the whole point of SCRUM-137:
**variety is not correctness.** The role field on this ledger is not missing
and not uniform. It is FILLED IN WRONGLY — Fidelity, TSP, Robinhood and
Coinbase each confidently assert `defaultAsset`, and three credit cards are
entered as asset accounts. No heuristic keyed on the SHAPE of the role
distribution can ever see that, because the distribution looks healthy. Nine
accounts agreeing on a wrong answer is not a signal of doubt.

**So the role field is no longer read as a claim about cash.** It is
self-declared metadata, entered by hand, and it has now been observed missing,
uniform and wrong. But it is not uniformly worthless, and the fix turns on the
distinction the first two attempts missed:

  * `savingAsset` and `cashWalletAsset` are DELIBERATE. Somebody opened the
    account and actively chose that role. Marcus and Cash wallet carry them.
  * `sharesAsset` and `ccAsset` are equally deliberate, in the other
    direction — an active "this is not cash".
  * **`defaultAsset` is Firefly's UNSET value.** It is what a new asset
    account gets when nobody chooses. On this ledger it is carried by nine
    accounts at once: two checking accounts, two brokerages, a TSP, a crypto
    account and three credit cards. That is not a ledger saying "these are all
    cash". It is a ledger that has not been asked.

So `defaultAsset` is treated as SILENCE, and silence is not counted. Only a
deliberate marking puts money in the pot. This can only ever UNDERSTATE the
runway — real cash sitting at the default is withheld, never inflated — which
is the safe direction, and the figure is published as a floor and says so.

Two things had to be checked against the live ledger before writing that, and
were (SCRUM-137, raw API output, not inference):

**The obvious rule is inverted here.** "Count the accounts spending actually
flows out of" sounds like behaviour beating metadata. Over 60 days all 112
withdrawals have a credit card as their source and the cash accounts have ZERO.
Anthony spends on cards. That rule would have selected exactly the three cards
as the numerator and excluded every real account — strictly worse than the bug.
Behaviour cannot supply this numerator; it is written down here so the next
attempt does not rediscover it the expensive way.

**Firefly is not hiding a better field.** `credit_card_type`, `liability_type`,
`liability_direction`, `current_debt` and `monthly_payment_date` are all null on
all eleven accounts, and all eleven are type `asset` — there are no liability
accounts at all. `/networth` is not discarding a signal; there is none.

**A negative balance is never spendable**, whatever role it claims. But it is
worth being exact about what that rule does and does not catch, because it was
briefly mistaken for a card detector: it fires on Discover only because Discover
opened at 109.58 and has since spent 746.26. The cards were entered as assets
whose opening balance is a statement figure, and charges decrement it like a
cash pot, so Platinum and Amex are on the same trajectory and will each cross
zero later. A rule that admits an account to `liquid` until its spending
happens to exceed its opening balance is a definition that moves silently as
the month goes on. Treating `defaultAsset` as silence is what actually keeps
the cards out of the numerator; the negative-balance rule is a backstop for
debts, not the classifier.

**Every account lands in exactly one visible bucket.** An input that vanishes
from the output cannot be argued with, and this module's whole claim to being
trustworthy is that its inputs are visible enough to argue with. Discover was
correctly dropped for a negative balance and then appeared in no list at all;
`test_every_account_is_visible_exactly_once` exists so that cannot recur.

**It lies if the burn is wrong.** A burn computed from a truncated read is too
small, which makes runway too long — the optimistic direction, which is the
dangerous one. So a partial window yields None, never a number. Same for a
stale ledger: spending that hasn't been imported hasn't stopped happening.

Income is split salary from resale so the picture can be read both ways: resale
is real income but it is lumpy and self-directed, and "how long do I last if
resale stops" is a different question from "how long do I last".
"""
from __future__ import annotations

# Roles that are a DELIBERATE statement that this money is spendable now.
# Someone chose these; they are not what an untouched account carries.
LIQUID_ROLES = ("savingAsset", "cashWalletAsset")
# Firefly's unset value for an asset account — what you get when nobody
# chooses. Not a claim about anything, so it is never counted. This is the
# whole SCRUM-137 fix: nine accounts sat here, including a TSP and three
# credit cards, and were read as an assertion that they were all cash.
UNSTATED_ROLE = "defaultAsset"
# Roles that are real money but not reachable this month without a decision.
# As deliberate as the liquid ones, in the other direction.
ILLIQUID_ROLES = ("sharesAsset", "ccAsset")

MIN_BURN = 1.0          # below this, months-of-runway is a divide-by-noise
MAX_MONTHS = 120        # past ten years the number stops meaning anything


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def split_accounts(accounts) -> dict:
    """Sort accounts into what you can spend, what you can't, what is owed, and
    what the ledger has simply not said anything about.

    That last bucket is the point. It is not a failure state and not an error:
    it is the honest reading of an account sitting at Firefly's default role.
    Money in it is never counted, so the runway built from the rest is a floor.
    """
    liquid, illiquid, unstated, unclassified, debts = [], [], [], [], []
    for a in accounts or []:
        if not isinstance(a, dict):
            continue
        row = {"name": a.get("name") or "", "balance": _num(a.get("balance")),
               "role": a.get("role")}
        if a.get("kind") == "liability" or row["balance"] < 0:
            # Either Firefly says it is a debt, or it is an "asset" carrying
            # one. A negative balance is not money you can spend, whatever
            # column it was entered in. A backstop, not the classifier.
            debts.append(row)
        elif row["role"] in ILLIQUID_ROLES:
            illiquid.append(row)
        elif row["role"] in LIQUID_ROLES:
            liquid.append(row)
        elif row["role"] == UNSTATED_ROLE:
            unstated.append(row)
        else:
            # A role no version of this module has heard of, or none at all.
            # Distinct from `unstated`: the fix is different, and so is what
            # it says about the ledger.
            unclassified.append(row)
    return {"liquid": liquid, "illiquid": illiquid, "unstated": unstated,
            "unclassified": unclassified, "debts": debts}


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
    # Money the ledger has not vouched for. Counting any of it could only
    # LENGTHEN the runway, which is what makes the published figure a floor
    # rather than a guess.
    withheld = buckets["unstated"] + buckets["unclassified"]

    reason = None
    if fr.get("window_complete", True) is False:
        # A truncated read UNDERSTATES spending, so it OVERSTATES runway. Of the
        # two directions to be wrong in, that is the one that costs you money.
        reason = "the spending read was truncated, so the burn would be too low"
    elif not buckets["liquid"]:
        # Nothing deliberately marked as cash. Which silence it is decides what
        # the reader should do about it, so they are not collapsed.
        if buckets["unstated"]:
            # The state this ledger is in, and the one that published 64.7
            # months: every account sits at Firefly's default role. Naming them
            # matters — the reader is the only one who can settle it, and this
            # is a five-minute fix in Firefly, not a bug to wait out.
            names = ", ".join(a["name"] for a in buckets["unstated"])
            reason = ("Firefly has no account marked as savings or cash — "
                      f"{names} are all sitting at the default role, which says "
                      "nothing about whether they are spendable, so dividing by "
                      "them would count brokerages as grocery money")
        else:
            reason = ("no account is marked as cash in Firefly, so there is "
                      "nothing to divide")
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
        # True => money exists that the ledger has not vouched for, so `months`
        # is a floor. Counting it could only lengthen the runway, never shorten.
        "lower_bound": bool(withheld),
        # Sitting at Firefly's default role. Separate from `unclassified`
        # because the fix differs: this one needs a role chosen, that one is a
        # value we do not recognise at all.
        "unstated": [a["name"] for a in buckets["unstated"]],
        "unclassified": [a["name"] for a in buckets["unclassified"]],
        "excluded": [a["name"] for a in buckets["illiquid"]],
        # Debts and "assets" carrying a debt. Present so that an account which
        # is dropped is still SEEN to be dropped — Discover fell out of the
        # payload entirely once, and an invisible input cannot be corrected.
        "debts": [a["name"] for a in buckets["debts"]],
        "income_salary": salary,
        "income_resale": resale,
        "months_without_resale": without_resale,
    }
