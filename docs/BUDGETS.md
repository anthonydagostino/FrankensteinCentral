# Budgets — model, formulas, and rules

FrankensteinCentral's budgeting layer turns Firefly III's transaction history
into forward-looking guidance. **Firefly stays the financial system of
record** — this layer stores only what Firefly can't provide: your monthly
limits and how Firefly categories map onto them.

## Data model

Budget definitions live in **core settings** (`budgets`), edited in
⚙ Settings → Monthly budgets:

```json
{"id": "dining", "name": "Dining", "limit": 300,
 "categories": ["Dining Out", "Restaurants"]}
```

- One budget maps to **one or more Firefly category names** (case-insensitive).
- Transactions come from the firefly service's `/month` endpoint each request
  (withdrawals + categorized deposits for the current local-time month;
  **transfers are excluded entirely** — moving money between your own accounts
  is neither spending nor income).
- The budget service (`/status`) is stateless with a 60s cache.

Why not Firefly's own budgets? Firefly budgets attach per-transaction
(a budget id on each txn), which requires tagging every transaction inside
Firefly. Category mapping works with data the CSV imports already carry. The
audit (`firefly /audit`) reports whether Firefly budgets exist so this call
can be revisited if the user starts using them.

## Formulas (engine.py — pure and unit-tested)

For a month of `D` days with `d` days elapsed (today included) and
`r = D − d` full days remaining:

| value | formula |
|---|---|
| spent | Σ withdrawals(mapped cats) − Σ categorized deposits(mapped cats) |
| remaining | limit − spent |
| pct | 100 · spent / limit |
| daily_rate | spent / d |
| projected (month-end) | daily_rate · D |
| projected_delta | projected − limit |
| safe_per_day | max(remaining, 0) / max(r, 1) |

Refunds: a **categorized deposit** counts as a refund/credit against that
category. Uncategorized deposits are income, never refunds. A net-negative
month (refunds > spend) renders as 0% full and healthy.

## Warning states (exact rules)

Evaluated in order; first match wins. All require a fresh ledger (below).

1. **OVER** — `spent > limit`
2. **APPROACHING** — `remaining ≤ 10% of limit`, OR
   (`r > 0` and `remaining > 0` and `safe_per_day < 0.5 × limit/D`)
   — i.e. staying on plan would require living on less than half the
   budget's implied daily rate.
3. **WATCH** — `projected > limit` **and** `d ≥ 3`
   (before day 3 the projection is too noisy; early-month WATCH is suppressed)
4. **HEALTHY** — everything else.

Severity for the attention feed: over/approaching → important, watch → fyi.
Warning copy is calm and states the numbers ("about $7.45/day keeps you on
plan"), never guilt.

## Freshness (zero ≠ unknown; synced ≠ spent)

Two distinct signals, never conflated:

- **`ingest_days`** — days since transaction data last **entered** Firefly.
  Evidence is transaction **`created_at` only**: Firefly stamps it when the
  record is written and never changes it, so importing an old-dated bank
  transaction today yields `created_at = today, date = 20 days ago` —
  precisely "imported today; activity 20 days ago". Querying Firefly never
  refreshes it. Two look-alike timestamps are **deliberately rejected**:
  transaction `updated_at` (bumped by ordinary edits — recategorizing an
  old transaction is not an import and must not revive stale guidance) and
  account `updated_at` (bumped by metadata changes with no financial
  ingestion). Firefly's API can't sort or filter by `created_at`, so the
  signal is gathered from the month's transactions plus a newest-dated
  ledger-wide probe — the strongest provenance the API exposes.
- **`activity_days`** — days since the newest transaction **date** of any
  type. This is *spending recency* — supporting info only, never a pause
  trigger on its own when an ingestion signal exists.

Rules (engine constants `INGEST_MAX_DAYS=3`, `ACTIVITY_FALLBACK_MAX=2`):

- `ingest_days < 3` → **ACTIVE**. A user who synced today but hasn't spent
  in 3 days stays active — the UI shows "Data synced today · last
  transaction 3 days ago". Not spending is not staleness.
- `ingest_days ≥ 3` → **PAUSED** with reason "financial data hasn't been
  imported for N days": spent/limit/remaining still display (true
  as-of-the-ledger), but daily rate, safe/day, projections, warnings and
  budget room are **null** — never 0, never "on track".
- No ingestion signal at all (old firefly build) → conservative fallback:
  active only if `activity_days < 2`, and the payload says
  `signal: "activity_fallback"` so the degradation is visible.

The paused state answers three questions rather than dead-ending: what's
wrong (financial data isn't current), why it matters (guidance is paused so
it can't mislead), and what to do (an **Import transactions ↗** action
deep-linking to the existing Firefly Data Importer — the importer is never
recreated or automated here).

### Two tolerances, on purpose

Day-level and month-level claims need different amounts of freshness, so
they use different thresholds:

| claim | threshold | why |
|---|---|---|
| "you spent $X today" (Money card) | ingest ≥ 2 days → suppressed | a same-day figure is unknowable if nothing was imported today; $0 would be a lie |
| month-to-date budget position + pace | ingest ≥ 3 days → PAUSED | a month-scale position survives a short lag, and the sync date is stated on screen |

So at `ingest_days = 2` the Money card correctly shows today as "—" with
"Financial data hasn't been imported for 2 days", while budgets stay ACTIVE
and disclose "Data synced 2 days ago". Both statements are true and the
user can see the lag; neither implies data we don't have.

## Budget Room

`budget_room = Σ max(limit − spent, 0)` across active budgets — over-budget
categories contribute 0, they do not offset others. Presented as
"$X remaining across active budgets": it is **remaining budget capacity**,
not a bank balance — it excludes unbudgeted categories, uncategorized
spending, and future bills. Suppressed entirely when ingestion is stale or
no budgets exist.

The label **"Safe to Spend" is reserved** for a future engine that also
accounts for upcoming bills, obligations and liquidity; Budget Room is one
component of that calculation and keeps its honest, narrower name until the
fuller engine exists.

## Uncategorized & unbudgeted spending

- Uncategorized withdrawals are never silently dropped: the Budget view shows
  the month's uncategorized total + count ("needs review in Firefly").
- If uncategorized ≥ 20% of the month's spend AND ≥ $50, a **low-confidence**
  flag states that category-budget conclusions may be off.
- Categorized-but-unbudgeted spending is listed ("Not in any budget: Gas $79")
  so nothing disappears between the budgets.

## The pay cycle — "what I spent" and "what's left to spend"

Monthly budgets answer *am I on plan for this category*. They do not answer
the two questions the homepage is actually asked:

1. **What did I spend this month?** — month-to-date withdrawals, **minus the
   money that only moved to savings**. A $1,100 transfer to Fidelity is not
   $1,100 of spending, and if the import books it as a plain withdrawal
   rather than a transfer it must still not read as one.
2. **How much of this paycheck is left?** — the paycheck that landed, minus
   the savings that come out of it, minus what has been spent since it
   landed.

Both are computed by `services/budget/app/paycheck.py` (pure,
unit-tested) from `firefly /cycle` — a read-only window that spans **both**
the current pay cycle and the calendar month, so the same
savings-vs-spending classification applies to both numbers instead of two
windows quietly disagreeing.

### Configuration (core settings → `paycheck`)

```json
{"enabled": true,
 "match": ["payroll", "direct dep"],
 "min_amount": 500,
 "cadence_days": 14,
 "allocations": [
   {"name": "Fidelity", "amount": 1100, "match": ["fidelity"],
    "already_withheld": false},
   {"name": "Marcus", "amount": 500, "match": ["marcus"]}]}
```

Matching is case-insensitive and looks at a transaction's **description,
source account, destination account and category** — a Firefly transfer is
often described just "Savings" while only the destination account says
"Fidelity", so the description alone is not enough.

### Formulas

| value | definition |
|---|---|
| paycheck | Σ matching deposits **on the most recent paycheck date** (split direct deposits are one paycheck) |
| allocation (each) | the **observed** matching transfer/withdrawal in the cycle if there is one; otherwise the **configured** amount, labelled `expected` |
| savings_total | Σ allocations (an `already_withheld` allocation contributes **0** — the deposit is already net of it) |
| spendable | paycheck − savings_total |
| spent | Σ withdrawals since the paycheck date, **excluding** allocation-matched ones |
| left | spendable − spent |
| per_day | max(left, 0) / days to next payday |
| month.spent | Σ month-to-date withdrawals excluding allocation-matched ones |
| month.savings | Σ allocation-matched outflows this month (shown, never counted as spending) |

The next payday is `last paycheck + cadence`, where cadence is the **observed**
gap between the last two paychecks when it is plausible (5–40 days) and the
configured `cadence_days` otherwise.

### Direction, not just names (PO review of `e7adf83`)

Firefly records both legs of every movement, so **which account the money left
and which it entered** is the only reliable signal of direction. Two P1 defects
shipped because matching searched every field indiscriminately:

- a $300 transfer **out of** savings was counted as a $300 contribution **to**
  it, and
- a grocery run **paid from** the savings account disappeared from spending
  entirely.

The rule now:

| what matched | meaning |
|---|---|
| destination only | **contribution** — money went into savings |
| source only | **reverse** — money came out; reported as `from_savings`, never added to spendable |
| both ends | same account; no net movement |
| neither (no account data at all) | fall back to description/category — the only case where a description may decide |

A description reading "Savings" says nothing about direction — the same word
appears on the way in and on the way out — so it is consulted **only** when
neither account is named.

**This means allocations should match on the account name, not the
description.** If a movement's description matches a rule but neither of its
accounts does, direction is unknowable: it is counted neither way and reported
as `unmatched_savings`, shown on the card. Ignoring it silently would
understate savings and push "left to spend" **up**, which is the dangerous
direction — the same class of error as the defect this section fixes, pointed
the other way.

**Each movement is claimed by at most one allocation**, in configuration
order. Two rules that both matched "savings" used to deduct the same transfer
twice, quietly halving what the card said was left. Overlapping configuration
is now surfaced as `allocation_overlaps` rather than silently producing a
smaller number.

### Withheld rules never claim a real movement

An allocation marked `already_withheld` describes money the employer took
**before** the deposit landed, so it deducts nothing by design. Single
assignment (above) originally took the first matching rule regardless, so a
pre-deposit rule could claim a genuine post-payday transfer and then deduct
zero — the contribution vanished and "left to spend" read high. Post-deposit
rules now get first refusal. If a real movement matches **only** a withheld
rule, the configuration is wrong: it is reported as
`withheld_rule_conflicts` and named on the card, never silently zeroed.

### A truncated window is not a total

`/cycle` pages Firefly under a cap. Hitting that cap means the window is a
**partial view**, not a small ledger, so `window.complete` is published and the
engine treats it exactly like a stale ledger.

**Every** money figure in the cycle goes `null`, not just the derived ones.
The error runs in both directions: missing withdrawals make `left` an
*overestimate*, while a missing deposit makes the paycheck itself wrong and
`left` an *underestimate*. So none of these numbers is a floor, and absent
completeness evidence reads as unknown rather than as proof that what was read
is all there is. `figures_complete: false` marks the whole block.

**A lower bound is not the same as unknown, and the difference is per-figure.**
Month-to-date spend from a truncated read can only grow — unread withdrawals
add to it — so the headline renders "**at least** $X" rather than throwing the
number away. `left` has no such property (missing withdrawals overstate it, a
missing deposit understates it), so it goes `null`. Month completeness is
carried **independently of the pay cycle**: a truncated window with no matching
paycheck takes the unavailable path and loses every cycle field, so a headline
relying on those would silently print a partial read as an exact total.

**The boundary case:** at exactly `cap × 50` rows every page read was full and
the cap is spent, so the next page might hold one more row or none. There is
no evidence either way, and no evidence must not read as complete — so that
case is reported incomplete. A short final page is the only proof the ledger
ended inside the window.

`/month` publishes the same signal, and the **monthly budget engine** consumes
it: a truncated month read understates spend, which would make every budget
look healthier than it is, so it pauses guidance with
`signal: "incomplete_window"`. Undercounting spending
confidently is the failure mode this prevents.

### What it refuses to claim

- **Expected ≠ observed.** An allocation the ledger hasn't seen yet still
  shapes the number — it is the user's stated plan — but it is always
  labelled `expected`, never presented as a transfer that happened.
- **Double-subtraction is guarded twice.** An allocation-matched withdrawal
  is savings, not spending, so the same $1,100 can never be taken out as a
  deduction *and* again as spend. `already_withheld` covers the opposite
  case (employer withholds pre-deposit, so the deposit is already net).
- **A missing paycheck is not an overspend.** Past `next payday +
  OVERDUE_GRACE_DAYS` with no newer deposit, `left` is `null` with the
  reason — a ledger that is behind is not a user who is $2,000 in the hole.
  The grace days exist because paydays drift across weekends.
- **Stale ingestion pauses guidance, not totals.** Same rule as budgets:
  `spent`/`left` still display (true as of the ledger, and the UI says
  through which date), while `per_day` goes `null`. With no ingestion signal
  at all, nothing is treated as fresh.
- **An empty month is unknown, not $0** — `month.spent` is `null` when
  nothing has been imported since the month began.
- **"Left to spend" is a pay-cycle figure, not a bank balance** and not
  "Safe to Spend": it does not know about bills due later in the cycle. It
  sits beside Budget Room on the Money card, both explicitly scoped.

## Bills

Firefly's own bills API is the source of truth (`firefly /bills` — name,
average amount, next expected date, paid-this-month). If the user hasn't
configured bills in Firefly, the section simply doesn't render
(`supported:false`); no parallel bill database exists here.

## Cash runway (runway.py — pure and unit-tested)

    liquid balances ÷ trailing average monthly burn = months of runway

Every other money figure here looks backwards. This is the only forward-looking
one, and the only one that changes a decision. The arithmetic is one division;
all the care is in the two ways it lies, **both of which fail optimistically**,
which is the direction that costs money.

**"Liquid" must be right**, and Firefly cannot say what is. This section is
mostly the record of four attempts to make it.

### The schema limit (SCRUM-137)

Firefly III's complete account-role vocabulary, verified twice — upstream
`config/firefly.php` and the running instance on the box:

    defaultAsset, sharedAsset, savingAsset, ccAsset, cashWalletAsset

Four cash-like values and a credit card. **No role means brokerage, investment
or retirement**; Firefly does not model investment accounts. So a TSP and a
current account are both `defaultAsset` and *no rule reading that field can
separate them*. This is a schema limit, not an uncurated ledger, and it is the
fact every failed attempt below tried to work around.

`defaultAsset` is doing three jobs at once: "nobody chose", "this is a checking
account", and "this is a brokerage". That is why it can be read as none of them.

**`sharesAsset` does not exist.** It sat in this codebase for a day as an
"illiquid role", so the exclusion branch never fired on real data and its tests
only proved it fired on a string we invented. It also produced an impossible
instruction to the user — "set your brokerages to sharesAsset" — for an option
that is not in the dropdown. `sharedAsset` is real and means a **joint
account**, which is cash: correcting the "typo" would start excluding real
money.

### Four attempts

| attempt | guards against | why it failed |
|---|---|---|
| trust `account_role` | a role that is MISSING | the role was present and **wrong** — published **64.7 months** against a true ~13.2 |
| suppress when all roles match | a role that is UNIFORM | written against an unchecked claim; the ledger has three roles, so it could never fire |
| treat `defaultAsset` as silence | a role that is UNSET | never converges — `defaultAsset` is the only correct value for a checking account, so it withholds them forever |
| require a liability to exist | an uncurated ledger | opens as soon as the cards are fixed, while the investments still count |

**Variety is not correctness.** Nine accounts agreeing on a wrong answer is not
a signal of doubt. And a rule whose behaviour on *perfect* data is still wrong
is the wrong rule, however safe its direction.

### What the module does now

Uncertainty is represented rather than resolved:

- `savingAsset` / `cashWalletAsset` — deliberate "this is cash". Counted.
- `ccAsset`, a Firefly liability, or a negative balance — a debt. Never counted.
- `defaultAsset` / `sharedAsset` — **ambiguous**. Upper bound only.

With nothing configured the answer is a **range** — "at least 7.9 months,
possibly 64.7" — and the width is the honest measure of how much the ledger has
not been asked. A range needs no gate, and every gate proposed here had a hole.
The card leads with the floor and states the ceiling as conditional: the high
end is the optimistic direction and the expensive one to anchor on.

`finance.not_spendable` names the accounts that are not cash. Supplying it is
the statement that the accounts have been reviewed, so everything else that is
not a debt is cash and the range collapses to one number. The classification
lives here because it cannot live in Firefly — object groups are not settable
on accounts via the API either, so there is no native home for it.

`months` stays `null` while the range is open. A consumer must never get half a
range and read it as the whole answer, which is how 64.7 reached the card.

### The performable ledger fix

**Mark the credit cards `account_role=ccAsset`** (Firefly requires
`credit_card_type` and `monthly_payment_date` alongside it). Zero code change —
they become debts immediately. Do **not** advise re-entering cards as Firefly
*liability* accounts: there is no asset→liability conversion in
`AccountUpdateService`, so it likely means delete and recreate, losing the
transaction history the burn window is computed from.

### Two findings that are expensive to rediscover

**The obvious behavioural rule is inverted.** "Count the accounts spending
actually flows out of" sounds like behaviour beating metadata. Over 60 days of
the real ledger, **all 112 withdrawals have a credit card as their source and
every cash account has zero** — the spending is entirely card-intermediated.
That rule selects exactly the three cards as the numerator.

**A negative balance is a debt backstop, not a card detector.** The cards were
entered as assets whose opening balance is a statement figure, and charges
decrement it, so each crosses zero at a different moment (Discover already has).
A rule that admits an account until its spending exceeds its opening balance is
a definition that moves silently as the month goes on.

**Every account lands in exactly one visible bucket** — `liquid_accounts`,
`ambiguous`, `excluded`, `debts`. Discover was once correctly dropped and then
appeared in *no* list at all. An input that vanishes cannot be argued with.

The card also names the accounts it *counted*, not only the ones it excluded.
`64.5` passed unexamined for a day; `Counting: Santander, Marcus, Fidelity,
TSP, Platinum Card…` would not have.

**The burn must be right.** A truncated read understates spending, so it
overstates runway — `window_complete: false` yields `null`, never a number. So
does a ledger that hasn't been imported for a week: spending that hasn't been
imported hasn't stopped happening. Under fourteen days of history there is no
month to average, and a near-zero trailing spend is refused rather than
rendered as infinite runway. Every suppression carries a `reason`, so the card
says *why* instead of going blank.

Income is split salary from resale, so "how long do I last" and "how long do I
last if resale stops" can both be read — resale is real income, but it is lumpy
and self-directed.

## The completeness flag: one name, `window_complete`

Every payload that can be truncated carries **`window_complete`** — at the top
level, under that exact name, on every endpoint that has it:

| service | endpoints |
|---|---|
| firefly | `/spending`, `/month`, `/cycle`, `/history` |
| budget | `/status`, `/paycheck` (top level, and inside `month` and `cycle`), `/recurring` |
| assistant | the `money` block of `/home` |

`false` means the page-cap walk stopped early, so the rows are a partial view
of the window and no total computed from them is a total. See *Freshness* for
what each consumer suppresses in response.

It used to be spelled five ways at once — `window_complete` on two firefly
endpoints, `window.complete` on the other two, `month.complete` and
`figures_complete` inside the paycheck payload, and `complete` on
`/recurring`. That is not cosmetic. Reading the wrong spelling returns `null`,
and **`null` is indistinguishable from "the read was fine"** unless the caller
already knows which name that particular endpoint chose. It caused a real
misread: a consumer asked `/recurring` for `window_complete`, got `null`
sitting next to `absence_claims_suppressed: false`, and could not tell whether
the honesty guard had failed or the key was simply named something else.

`tests/test_wire_names.py` is the guard. It fails if any service publishes or
reads the concept under another name, if the flag moves back inside firefly's
`window` object, or if a producer stops publishing it at all — the last one
because a guard that only bans names would also pass if the flag vanished.

## Recurring charges (recurring.py — pure and unit-tested)

Firefly holds every transaction, so recurrence is a property of the system
rather than a script you run by hand. `firefly /history` reads 13 months of
**withdrawals** (transfers are not purchases, and the $1,100 to Fidelity every
payday is a perfect monthly pattern that is not a subscription) plus Firefly's
declared bills; `budget /recurring` turns that into an inventory and, more
importantly, into **events**.

| event | claim |
|---|---|
| `appeared` | something started charging you that wasn't charging before |
| `changed` | a known charge moved price |
| `resumed` | one you believed cancelled charged again after a gap |

The card shows events only. The value of the feature is its false-positive
rate: a card that announces a new subscription every time you buy coffee twice
gets ignored, and an ignored card is worse than none — the month something
real appears, you scroll past that too. So the rules are refusals:

- **A cadence must be a rhythm.** The median interval sets it; every other
  interval must be that rhythm or a whole multiple of it (a skipped month is
  not a disproof). Anything else — three days when the rhythm is thirty — is a
  merchant you use often, not a commitment, and it is dropped entirely rather
  than reported at low confidence.
- **"Appeared" requires history before it.** If the first charge sits within a
  cadence of `window.start`, the honest answer is "I can't see far enough
  back", not "this is new". `history_before_days` carries the evidence.
- **"New" has a shelf life** (`APPEARED_WINDOW_DAYS`), and a cadence too slow
  to establish itself inside that shelf life can never be new. This is what
  kills the signature false positive of the genre — an annual renewal seen
  twice, announced as a brand-new subscription — and it kills it by
  arithmetic, not by demanding a third charge that a monthly subscription
  would never produce in time.
- **A resumption is only news while it is fresh.** The gap stays in the
  history forever; without this, the card would announce "it came back!" every
  day for the rest of time.
- **A price change must clear both a relative and an absolute floor**
  (2% *and* $0.50), so card-rounding drift is not an announcement and a $1.99
  charge doesn't "change price" over a dime. The established price is the
  **mode**, not the mean and not the latest, so one promotional month does not
  redefine what a thing costs.
- **A bill declared in Firefly is never a discovery.** Firefly stays the
  source of truth; the detector only reports what the user hasn't already
  said.

### Honesty under a truncated read

Same rule as everywhere else here (see *Freshness*): `window.complete = false`
suppresses `appeared` and `resumed` **entirely**, because both are claims
about what *isn't* in the data and a partial read cannot support one. Price
changes survive — those are two charges that were actually read. The response
says `absence_claims_suppressed: true` rather than implying there were none,
and the card drops the "$X/mo across N subscriptions" line rather than
publishing a floor as a total.

Low-confidence items (two charges — a cadence, but only one interval) are
listed and hedged on screen, and are **excluded** from the monthly-equivalent
total rather than estimated into it.

## Future path (architected, not built)

- **Month templates / irregular months**: budgets are evaluated against the
  current month context (`D/d/r` passed in, not assumed) — per-month limit
  overrides can be added as `{month: "2026-09", limit: …}` entries without
  reshaping the engine.
- **Savings goals**: a goal is a budget with direction reversed (fill = good).
  The engine's inputs (limits + net flows by category) already support it;
  a `goals` list in core settings plus a vessel variant is the clean path.
- The month boundary uses LOCAL_TZ; leap years and 28–31-day months come from
  `calendar.monthrange` (tested for 28/29/30/31).
