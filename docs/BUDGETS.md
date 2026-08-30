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

## Freshness (zero ≠ unknown)

`days_stale` = days since the newest transaction of ANY type in Firefly.

- `days_stale < 2` → current guidance allowed.
- otherwise → every budget's state is **PAUSED**: spent/limit/remaining still
  display (they are true as-of-the-ledger), but daily rate, safe/day,
  projections, warnings and safe-to-spend are **null** — never 0, never
  "on track". The UI shows "Budget tracking paused — data last updated N
  days ago."

## Safe to spend

`safe_to_spend = Σ max(limit − spent, 0)` across active budgets — over-budget
categories contribute 0, they do not offset others. It is **always labeled
"across active budgets"**: it is budget capacity, not a bank balance, and it
excludes unbudgeted categories, uncategorized spending, and future bills.
Suppressed entirely when the ledger is stale or no budgets exist.

## Uncategorized & unbudgeted spending

- Uncategorized withdrawals are never silently dropped: the Budget view shows
  the month's uncategorized total + count ("needs review in Firefly").
- If uncategorized ≥ 20% of the month's spend AND ≥ $50, a **low-confidence**
  flag states that category-budget conclusions may be off.
- Categorized-but-unbudgeted spending is listed ("Not in any budget: Gas $79")
  so nothing disappears between the budgets.

## Bills

Firefly's own bills API is the source of truth (`firefly /bills` — name,
average amount, next expected date, paid-this-month). If the user hasn't
configured bills in Firefly, the section simply doesn't render
(`supported:false`); no parallel bill database exists here.

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
