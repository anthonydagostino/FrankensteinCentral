"""Paycheck-cycle engine — pure, deterministic calculations. No I/O.

Answers the two questions the monthly-budget engine deliberately does not:

  1. **What have I spent this month?** — month-to-date withdrawals, with
     money moved to savings taken back out, so a $1,100 transfer to Fidelity
     never reads as $1,100 of spending.
  2. **How much of this paycheck is left to spend?** — the paycheck that
     landed, minus the savings that come out of it, minus what has been spent
     since it landed.

Both are *cycle* answers, not calendar ones: the pay cycle runs from the last
paycheck deposit to the next expected one, which is why this lives beside the
monthly budget engine rather than inside it. Monthly budgets stay monthly.

Inputs (all plain data, supplied by the firefly service's /cycle endpoint):
  cfg         : {"enabled", "match", "min_amount", "cadence_days",
                 "allocations": [{"name", "amount", "match", "already_withheld"}]}
  today       : ISO date string or date
  month       : {"label", "start", "days_total", "days_elapsed", "days_left"}
  deposits    : [{date, desc, amount(+), source, destination, category}]
  withdrawals : [{date, desc, amount(+), source, destination, category}]
  transfers   : [{date, desc, amount(+), source, destination, category}]
  freshness   : {"ingest_days", "activity_days", "month_ingested",
                 "ledger_latest_txn"}

Honesty rules (docs/BUDGETS.md), applied here too:
  * zero and unknown are different — suppressed values are None, never 0;
  * a total that is true as of the ledger still displays when ingestion is
    stale, but forward guidance ($/day) is suppressed and the staleness is
    named;
  * a cycle that cannot be established (no paycheck found, or the next
    paycheck is overdue and missing from the ledger) reports that instead of
    computing a confident number from an incomplete window.
"""
from __future__ import annotations

from datetime import date, timedelta

from .engine import INGEST_MAX_DAYS

DEFAULT_CADENCE_DAYS = 14      # biweekly, the most common US pay schedule
MIN_OBSERVED_CADENCE = 5       # shorter gaps are split deposits, not two cycles
MAX_OBSERVED_CADENCE = 40      # longer gaps are a missed import, not a cadence
OVERDUE_GRACE_DAYS = 3         # paydays drift across weekends/holidays
LOW_LEFT_PCT = 0.15            # <= this share of the spendable pot => "low"


def _as_date(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _amount(t: dict) -> float:
    try:
        return abs(float(t.get("amount") or 0))
    except (TypeError, ValueError):
        return 0.0


def _terms(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.split(",")
    return [t.strip().lower() for t in (raw or []) if str(t).strip()]


def _haystack(t: dict) -> str:
    """Everything that can name a counterparty. The description alone is not
    enough — a Firefly transfer is often described "Savings" while only the
    destination account says "Fidelity"."""
    return " ".join(str(t.get(k) or "") for k in
                    ("desc", "source", "destination", "category")).lower()


def _matches(t: dict, terms: list[str]) -> bool:
    if not terms:
        return False
    hay = _haystack(t)
    return any(term in hay for term in terms)


def _in(t: dict, start: date, end: date) -> bool:
    d = _as_date(t.get("date"))
    return d is not None and start <= d <= end


def _round(v):
    return None if v is None else round(v + 0.0, 2)


def _unconfigured(reason: str, configured: bool = False) -> dict:
    return {"configured": configured, "available": False, "reason": reason,
            "month": None, "cycle": None}


def paycheck_cycle(cfg: dict, today, month: dict, deposits: list,
                   withdrawals: list, transfers: list, freshness: dict) -> dict:
    cfg = cfg if isinstance(cfg, dict) else {}
    fr = freshness if isinstance(freshness, dict) else {}
    today = _as_date(today) or date.today()
    month = month or {}
    deposits, withdrawals, transfers = deposits or [], withdrawals or [], transfers or []

    configured = bool(cfg) and cfg.get("enabled", True) is not False
    if not configured:
        return _unconfigured("paycheck tracking is off")

    # ---- freshness -------------------------------------------------------
    ingest_days = fr.get("ingest_days")
    activity_days = fr.get("activity_days")
    month_ingested = fr.get("month_ingested")
    fresh = ingest_days < INGEST_MAX_DAYS if ingest_days is not None else False
    stale_reason = None
    if ingest_days is None:
        stale_reason = "no import evidence in the ledger, so guidance is paused"
    elif not fresh:
        stale_reason = (f"financial data hasn't been imported for {ingest_days} days, "
                        "so anything spent since then is missing")

    # ---- allocations: the money that leaves the pot on payday -------------
    allocs_cfg = [a for a in (cfg.get("allocations") or []) if isinstance(a, dict)]
    alloc_terms: list[str] = []
    for a in allocs_cfg:
        alloc_terms += _terms(a.get("match")) or _terms([a.get("name")])

    def is_savings(t: dict) -> bool:
        return _matches(t, alloc_terms)

    # ---- month to date ---------------------------------------------------
    month_start = _as_date(month.get("start")) or today.replace(day=1)
    month_wd = [t for t in withdrawals if _in(t, month_start, today)]
    month_spent = sum(_amount(t) for t in month_wd if not is_savings(t))
    month_savings = (sum(_amount(t) for t in month_wd if is_savings(t))
                     + sum(_amount(t) for t in transfers
                           if _in(t, month_start, today) and is_savings(t)))
    days_elapsed = int(month.get("days_elapsed") or today.day) or 1
    month_out = {
        "label": month.get("label") or today.strftime("%B %Y"),
        "start": month_start.isoformat(),
        "days_total": month.get("days_total"),
        "days_elapsed": days_elapsed,
        "days_left": month.get("days_left"),
        # An empty month with nothing imported computes to $0. That is
        # arithmetic, not knowledge — say unknown.
        "spent": None if month_ingested is False else _round(month_spent),
        "savings": None if month_ingested is False else _round(month_savings),
        "daily_avg": (None if month_ingested is False
                      else _round(month_spent / days_elapsed)),
    }

    # ---- find the paycheck ----------------------------------------------
    match_terms = _terms(cfg.get("match"))
    try:
        min_amount = float(cfg.get("min_amount") or 0)
    except (TypeError, ValueError):
        min_amount = 0.0
    pays = []
    for t in deposits:
        d = _as_date(t.get("date"))
        if d is None or d > today:
            continue
        if _amount(t) < min_amount:
            continue
        if match_terms and not _matches(t, match_terms):
            continue
        pays.append({**t, "_d": d, "_amt": _amount(t)})
    pays.sort(key=lambda t: t["_d"], reverse=True)

    if not pays:
        if not deposits:
            why = "there are no deposits at all in the window"
        elif match_terms:
            why = ("no deposit matched "
                   + ", ".join(f'"{m}"' for m in match_terms)
                   + " — check the paycheck settings against how your bank "
                     "describes it")
        else:
            why = f"no deposit was at least ${min_amount:,.0f}"
        return {"configured": True, "available": False, "month": month_out,
                "cycle": None,
                "fresh": fresh, "ingest_days": ingest_days,
                "activity_days": activity_days,
                "as_of": fr.get("ledger_latest_txn"),
                "reason": f"no paycheck found in the ledger — {why}"}

    last_date = pays[0]["_d"]
    # Split direct deposits land the same day as several rows; they are one
    # paycheck.
    same_day = [p for p in pays if p["_d"] == last_date]
    paycheck_amount = round(sum(p["_amt"] for p in same_day), 2)

    # ---- cadence and the next payday -------------------------------------
    prior = [p["_d"] for p in pays if p["_d"] < last_date]
    cadence_src = "configured"
    try:
        cadence = int(cfg.get("cadence_days") or DEFAULT_CADENCE_DAYS)
    except (TypeError, ValueError):
        cadence = DEFAULT_CADENCE_DAYS
    if prior:
        gap = (last_date - max(prior)).days
        if MIN_OBSERVED_CADENCE <= gap <= MAX_OBSERVED_CADENCE:
            cadence, cadence_src = gap, "observed"
    cadence = max(1, cadence)
    next_payday = last_date + timedelta(days=cadence)
    days_to_next = (next_payday - today).days
    # Past the grace period with no newer paycheck in the ledger, the cycle
    # window is not the one the user is living in — most likely a paycheck
    # simply hasn't been imported yet.
    overdue = days_to_next < -OVERDUE_GRACE_DAYS

    # ---- allocations for this cycle --------------------------------------
    cycle_out_txns = [t for t in (withdrawals + transfers) if _in(t, last_date, today)]
    allocations = []
    savings_total = 0.0
    for a in allocs_cfg:
        terms = _terms(a.get("match")) or _terms([a.get("name")])
        try:
            planned = float(a.get("amount") or 0)
        except (TypeError, ValueError):
            planned = 0.0
        seen = [t for t in cycle_out_txns if _matches(t, terms)]
        observed = round(sum(_amount(t) for t in seen), 2)
        withheld = bool(a.get("already_withheld"))
        if withheld:
            # The employer takes it before the deposit lands, so the paycheck
            # is already net of it. Showing it is useful; subtracting it again
            # is double-counting.
            amount, src = 0.0, "withheld_before_deposit"
        elif seen:
            amount, src = observed, "observed"
        else:
            amount, src = round(planned, 2), "expected"
        savings_total += amount
        allocations.append({
            "name": a.get("name") or "Savings",
            "amount": amount,
            "planned": round(planned, 2),
            "observed": observed if seen else None,
            "source": src,
            "date": max((_as_date(t.get("date")).isoformat() for t in seen),
                        default=None) if seen else None,
        })

    spendable = round(paycheck_amount - savings_total, 2)
    spent = round(sum(_amount(t) for t in withdrawals
                      if _in(t, last_date, today) and not is_savings(t)), 2)
    left = None if overdue else round(spendable - spent, 2)

    # ---- guidance (only when the ledger can support it) ------------------
    per_day = None
    if left is not None and fresh and days_to_next > 0:
        per_day = round(max(left, 0.0) / days_to_next, 2)

    if overdue:
        state = "unknown"
    elif left < 0:
        state = "over"
    elif spendable > 0 and left <= LOW_LEFT_PCT * spendable:
        state = "low"
    else:
        state = "ok"

    money = lambda v: f"${v:,.0f}"  # noqa: E731
    if overdue:
        text = (f"A paycheck was expected around {next_payday.isoformat()} and isn't in "
                "the ledger yet, so what's left of it can't be worked out. "
                "Import your latest transactions.")
    elif state == "over":
        text = (f"You're {money(-left)} past the {money(spendable)} this paycheck left "
                f"you to spend, with {max(days_to_next, 0)} day(s) until the next one.")
    elif state == "low":
        text = (f"{money(left)} left of this paycheck's {money(spendable)} with "
                f"{max(days_to_next, 0)} day(s) to go"
                + (f" — about ${per_day:,.2f}/day." if per_day is not None else "."))
    else:
        text = (f"{money(left)} left to spend before {next_payday.isoformat()}"
                + (f" — about ${per_day:,.2f}/day." if per_day is not None else "."))

    return {
        "configured": True,
        "available": True,
        "reason": None,
        "fresh": fresh,
        "stale_reason": stale_reason,
        "ingest_days": ingest_days,
        "activity_days": activity_days,
        "as_of": fr.get("ledger_latest_txn"),
        "month": month_out,
        "cycle": {
            "start": last_date.isoformat(),
            "days_elapsed": (today - last_date).days + 1,
            "paycheck": paycheck_amount,
            "paycheck_desc": pays[0].get("desc") or "",
            "paycheck_parts": len(same_day),
            "allocations": allocations,
            "savings_total": round(savings_total, 2),
            "spendable": spendable,
            "spent": spent,
            "left": left,
            "per_day": per_day,
            "next_payday": next_payday.isoformat(),
            "days_to_next": days_to_next,
            "cadence_days": cadence,
            "cadence_source": cadence_src,
            "overdue": overdue,
            "state": state,
            "text": text,
            "txns": sorted(
                [{"date": t.get("date"), "desc": t.get("desc"),
                  "amount": _amount(t), "category": t.get("category"),
                  "savings": is_savings(t)}
                 for t in withdrawals if _in(t, last_date, today)],
                key=lambda t: t["date"], reverse=True)[:20],
        },
    }
