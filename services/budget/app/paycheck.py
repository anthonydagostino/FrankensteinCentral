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
    """Does this movement mention a term ANYWHERE? Direction-blind on purpose.

    Only correct for questions where direction is meaningless — "is this
    deposit the paycheck". For anything about savings, use `_side`: a ledger
    movement that merely NAMES a savings account tells you nothing about which
    way the money went.
    """
    if not terms:
        return False
    hay = _haystack(t)
    return any(term in hay for term in terms)


def _names(t: dict, keys: tuple, terms: list[str]) -> bool:
    hay = " ".join(str(t.get(k) or "") for k in keys).lower()
    return any(term in hay for term in terms)


# Which side of a movement names a savings target. This is the whole fix for
# the direction bug: `_matches` searched source and destination together, so
# money moving OUT of savings looked exactly like money moving IN, and a
# grocery run funded from a savings account was subtracted from spending
# because the word "Savings" appeared somewhere in the row.
INTO, OUT_OF, INTERNAL, NEITHER = "into", "out_of", "internal", "neither"


def _side(t: dict, terms: list[str]) -> str:
    """Where the savings account sits in this movement.

      into      destination is savings, source is not  -> a contribution
      out_of    source is savings, destination is not  -> a REVERSE movement,
                or an ordinary purchase funded from savings
      internal  both sides are savings                 -> savings to savings
      neither   no account names it

    The account fields decide. `desc`/`category` are consulted only when
    NEITHER account names a term, because a Firefly transfer is often
    described "Savings" while only the destination account says "Fidelity" —
    that fallback is why the original code looked at the description at all,
    and it stays. What changes is that an account naming savings on the SOURCE
    side now overrules a description that says otherwise.
    """
    if not terms:
        return NEITHER
    to_savings = _names(t, ("destination",), terms)
    from_savings = _names(t, ("source",), terms)
    if to_savings and from_savings:
        return INTERNAL
    if to_savings:
        return INTO
    if from_savings:
        return OUT_OF
    if _names(t, ("desc", "category"), terms):
        return INTO
    return NEITHER


def _is_contribution(t: dict, terms: list[str]) -> bool:
    """Money genuinely ARRIVING in savings, and nothing else.

    Deliberately excludes INTERNAL (savings -> savings shuffles move nothing
    into savings from the spendable pot) and OUT_OF.
    """
    return _side(t, terms) == INTO


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

    # ---- completeness ----------------------------------------------------
    # The ledger window is read page by page under a cap. Reaching that cap
    # means there is more in Firefly than was read, and a sum over a partial
    # window is not a smaller true number — it is a wrong one. Absent fields
    # mean "complete", which is what their absence meant before they existed.
    complete = fr.get("complete") or {}
    wd_ok = complete.get("withdrawals", True)
    dep_ok = complete.get("deposits", True)
    tr_ok = complete.get("transfers", True)
    partial = [name for name, ok in (("spending", wd_ok), ("deposits", dep_ok),
                                     ("transfers", tr_ok)) if not ok]
    partial_reason = None
    if partial:
        partial_reason = (
            "the ledger holds more " + " and ".join(partial)
            + " than this window could read, so the totals that depend on "
              "them can't be worked out")

    # ---- allocations: the money that leaves the pot on payday -------------
    allocs_cfg = [a for a in (cfg.get("allocations") or []) if isinstance(a, dict)]
    alloc_terms: list[str] = []
    for a in allocs_cfg:
        alloc_terms += _terms(a.get("match")) or _terms([a.get("name")])

    def is_savings(t: dict) -> bool:
        """Money ARRIVING in savings. Direction matters — see `_side`."""
        return _is_contribution(t, alloc_terms)

    # ---- month to date ---------------------------------------------------
    month_start = _as_date(month.get("start")) or today.replace(day=1)
    month_wd = [t for t in withdrawals if _in(t, month_start, today)]
    # Only a genuine contribution is taken back out of spending. A purchase
    # funded FROM a savings account is still a purchase: it used to vanish
    # from `spent` AND be added to `savings`, so one grocery run moved the
    # figures in both directions at once.
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
        # Unknown and zero are different states (docs/BUDGETS.md). An
        # un-ingested month and a truncated read are both unknown.
        "spent": (None if month_ingested is False or not wd_ok
                  else _round(month_spent)),
        "savings": (None if month_ingested is False or not (wd_ok and tr_ok)
                    else _round(month_savings)),
        "daily_avg": (None if month_ingested is False or not wd_ok
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

    # Each movement is assigned to AT MOST ONE allocation, first match wins in
    # configured order. Previously every rule scanned every transaction
    # independently, so two rules that both matched "savings" each claimed the
    # same transfer and `savings_total` came out double — one ledger movement
    # deducted twice from what the paycheck left to spend.
    #
    # First-match-wins is a policy, not an accident, so it is reported: any
    # rule that WOULD have matched an already-claimed movement is named in
    # `ambiguous`, rather than the collision being resolved in silence.
    alloc_rules = []
    for a in allocs_cfg:
        alloc_rules.append((a, _terms(a.get("match")) or _terms([a.get("name")])))

    claimed: dict[int, int] = {}          # index in cycle_out_txns -> rule index
    contested: list[set] = [set() for _ in alloc_rules]
    for ti, t in enumerate(cycle_out_txns):
        if not _is_contribution(t, alloc_terms):
            continue                       # direction: only real contributions
        for ri, (_, terms) in enumerate(alloc_rules):
            if not _is_contribution(t, terms):
                continue
            if ti in claimed:
                contested[claimed[ti]].add(alloc_rules[ri][0].get("name") or "Savings")
            else:
                claimed[ti] = ri

    # Money that left savings during the cycle. Never a contribution, never
    # silently dropped: reported so a reverse movement is visible instead of
    # being counted as its own opposite.
    reverse = [t for t in transfers if _in(t, last_date, today)
               and _side(t, alloc_terms) == OUT_OF]
    reverse_total = round(sum(_amount(t) for t in reverse), 2)

    allocations = []
    savings_total = 0.0
    for ri, (a, terms) in enumerate(alloc_rules):
        try:
            planned = float(a.get("amount") or 0)
        except (TypeError, ValueError):
            planned = 0.0
        seen = [cycle_out_txns[ti] for ti, owner in claimed.items() if owner == ri]
        seen.sort(key=lambda t: _as_date(t.get("date")) or last_date)
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
            # Other rules that also matched a movement this one claimed.
            # Non-empty means the configuration is ambiguous and the split
            # between these goals is a guess, not a reading.
            "ambiguous": sorted(contested[ri]),
        })

    spendable = round(paycheck_amount - savings_total, 2)
    spent = round(sum(_amount(t) for t in withdrawals
                      if _in(t, last_date, today) and not is_savings(t)), 2)
    # `left` subtracts spending from a pot sized by savings, so it is only as
    # trustworthy as the least complete of the two reads. A partial deposit
    # list is worse still: the paycheck that anchors the whole cycle may be
    # the one that was not read.
    if overdue or partial:
        left, spent_out = None, (None if not wd_ok else spent)
    else:
        left, spent_out = round(spendable - spent, 2), spent

    # ---- guidance (only when the ledger can support it) ------------------
    per_day = None
    if left is not None and fresh and days_to_next > 0:
        per_day = round(max(left, 0.0) / days_to_next, 2)

    if overdue or partial:
        state = "unknown"
    elif left < 0:
        state = "over"
    elif spendable > 0 and left <= LOW_LEFT_PCT * spendable:
        state = "low"
    else:
        state = "ok"

    money = lambda v: f"${v:,.0f}"  # noqa: E731
    if partial:
        text = ("Only part of this window's " + " and ".join(partial)
                + " could be read from the ledger, so what's left of this "
                  "paycheck can't be worked out from it.")
    elif overdue:
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
        # Named separately from staleness: stale data is old but whole, a
        # partial read is incomplete but current. They need different fixes.
        "partial_reason": partial_reason,
        "window_complete": not partial,
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
            # Money that came BACK OUT of savings this cycle. Reported rather
            # than netted off `savings_total`: a withdrawal from savings is a
            # different event from a smaller contribution, and collapsing the
            # two would hide it.
            "reverse_from_savings": reverse_total,
            "spendable": spendable,
            "spent": spent_out,
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
