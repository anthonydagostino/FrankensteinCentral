"""Pure helpers for what the home screen shows.

Kept out of `main.py` deliberately: `main.py` imports psycopg and FastAPI, so
anything defined there can only be tested with a database driver installed.
These functions are the two places the dashboard was reporting things it did
not know, so they are exactly the parts that need tests that run everywhere.

Nothing here does I/O.
"""
from datetime import datetime


def parse_event_dt(raw, local_tz):
    """An event timestamp as an aware datetime in `local_tz`, or None.

    A naive string is read as local, not UTC. The schedule service stores
    whatever the calendar handed it, and reading a naive 9pm as UTC would move
    an evening event to the following day in New York — the same class of bug
    `docs/TESTING.md` was written about.
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone(local_tz)


def upcoming_events(events, now, local_tz, limit=6, statuses=None):
    """Events that have not finished yet, soonest first.

    `GET /events` returns every event ever stored, ascending by `starts_at`,
    with no lower bound — so `events[0]` is the OLDEST record on file, not the
    next thing happening. Every caller that wants "next" has to bound it here,
    or it reports history as if it were the future.

    An event still counts as upcoming while it is running: `ends_at` decides
    when it is present, so a meeting you are in the middle of does not vanish
    from the page halfway through. Unparseable timestamps are dropped rather
    than guessed at, and `declined` is filtered by the caller.

    `statuses`, when given, keeps only those statuses — the recommendation
    rules act on confirmed commitments, while the calendar card deliberately
    shows pending and countered holds too.
    """
    dated = []
    for e in events or []:
        if statuses is not None and e.get("status", "confirmed") not in statuses:
            continue
        start = parse_event_dt(e.get("starts_at") or e.get("start"), local_tz)
        if start is None:
            continue
        end = parse_event_dt(e.get("ends_at"), local_tz) or start
        if end < now:
            continue
        dated.append((start, e))
    dated.sort(key=lambda pair: pair[0])
    picked = [e for _, e in dated]
    return picked[:limit] if limit else picked


def firefly_state(firefly):
    """`ok`, `unreachable` or `not_configured` — three states, not two.

    The assistant's `_get` swallows a timeout and returns `{}`, so an empty
    payload means the service could not be reached. That is NOT the same as
    Firefly answering that it has no credentials. Collapsing both into "not
    connected" tells you to set FIREFLY_URL every time a container blinks,
    which sends you to repair configuration that is already correct.
    """
    if not firefly:
        return "unreachable"
    if firefly.get("connected") is False:
        return "not_configured"
    return "ok"


def deadline_rows(rows, now, local_tz, limit=6):
    """Split stored deadlines into overdue and upcoming, newest pressure first.

    WHY THIS EXISTS: the assistant extracts interview times and bill due dates
    on every sync and files them in `deadlines`, and the only page that could
    display them was `/space` -- the legacy lounge that was deliberately
    demoted. So the extraction ran, the rows accumulated, and nothing the user
    looks at could show them.

    Three states, not two, for the same reason the money layer draws them:

      * OVERDUE is not the same as upcoming and must not be sorted in with it.
      * A row with no `due_at` is UNDATED, not due today. The extractor stores
        a null when it could not find a date, and rendering that as "due now"
        would invent a deadline the email never carried.

    Everything is bucketed against the caller's `now`, so the tests can pin any
    date rather than depending on when they run.
    """
    overdue, upcoming, undated = [], [], []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip()
        if not title:
            continue
        item = {"title": title, "source": r.get("source"), "due_at": r.get("due_at")}
        due = parse_event_dt(r.get("due_at"), local_tz) if r.get("due_at") else None
        if due is None:
            undated.append(item)
            continue
        item["due_at_parsed"] = due
        (overdue if due < now else upcoming).append(item)
    overdue.sort(key=lambda i: i["due_at_parsed"], reverse=True)   # most overdue first
    upcoming.sort(key=lambda i: i["due_at_parsed"])                # soonest first
    for i in overdue + upcoming:
        i.pop("due_at_parsed", None)
    return {
        "overdue": overdue[:limit],
        "upcoming": upcoming[:limit],
        "undated": undated[:limit],
        "counts": {"overdue": len(overdue), "upcoming": len(upcoming),
                   "undated": len(undated)},
    }


def portfolio_alerts(stocks, threshold_pct):
    """Positions and watchlist symbols that moved at least `threshold_pct` today.

    WHY THIS EXISTS: Settings has had an "Alert on move >= (%)" field since the
    market section was written. It saved to `core`, round-tripped correctly,
    and was read by absolutely nothing -- so the alert it promised was never
    produced. A setting that silently does nothing is worse than a missing one:
    you configure it, you believe it is on, and you stop watching for the thing
    it was supposed to catch.

    Returns [] when the threshold is unset or unusable rather than defaulting
    to some other number -- an alert the user did not ask for is its own kind
    of lie about what the setting does.
    """
    try:
        threshold = abs(float(threshold_pct))
    except (TypeError, ValueError):
        return []
    if not threshold:
        return []
    seen, out = set(), []
    groups = ((stocks or {}).get("positions") or [],
              (stocks or {}).get("watchlist") or [])
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol")
            pct = item.get("change_pct")
            if not symbol or symbol in seen:
                continue
            try:
                pct = float(pct)
            except (TypeError, ValueError):
                continue          # no quote is not a move; it is no data
            if abs(pct) + 1e-9 >= threshold:
                seen.add(symbol)
                out.append({"symbol": symbol, "change_pct": pct,
                            "direction": "up" if pct >= 0 else "down"})
    out.sort(key=lambda a: abs(a["change_pct"]), reverse=True)
    return out


def low_balance_accounts(accounts, floor):
    """Accounts at or below the configured `finance.low_balance` floor.

    Same story as `portfolio_alerts`: `low_balance` has been in
    DEFAULT_SETTINGS and consumed by nothing.

    A missing balance is skipped, never treated as 0 -- an account whose
    balance could not be read is not an account that is empty, and that
    distinction is the whole of docs/BUDGETS.md.
    """
    try:
        limit = float(floor)
    except (TypeError, ValueError):
        return []
    out = []
    for a in accounts or []:
        if not isinstance(a, dict):
            continue
        balance = a.get("balance")
        if balance is None:
            continue
        try:
            balance = float(balance)
        except (TypeError, ValueError):
            continue
        if balance <= limit:
            out.append({"name": a.get("name") or "account", "balance": balance})
    out.sort(key=lambda a: a["balance"])
    return out
