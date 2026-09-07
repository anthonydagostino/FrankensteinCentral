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


def month_spend_claim(spending, paycheck):
    """`(value, is_lower_bound)` for the homepage's "spent this month" headline.

    Two different services can answer this, and they do not share a window, so
    whose completeness applies depends on which number was used:

      * the pay-cycle engine, when a paycheck is configured AND found — its
        figure has savings transfers taken back out, which is the more truthful
        answer to "what did I spend";
      * `/spending`'s calendar month otherwise.

    Both can be truncated by the ledger page cap. Whichever one is quoted, its
    own `window_complete` decides whether this is a total or a floor, and
    absent means unknown — which is a lower bound, not a clean total.

    This lives here rather than in `main.py` for the reason the module exists:
    `main.py` needs psycopg to import, so anything decided there cannot be
    tested. The bug being guarded against — a truncated month rendered as an
    exact total — is invisible to a test that cannot run.
    """
    spending = spending or {}
    paycheck = paycheck or {}
    pay_month = paycheck.get("month") or {}

    if paycheck.get("configured") and pay_month.get("spent") is not None:
        return pay_month.get("spent"), bool(pay_month.get("spent_is_lower_bound"))

    if not spending:
        # Nothing to quote, so nothing to qualify.
        return None, False
    if spending.get("month_ingested") is False:
        # A brand-new month with nothing imported is unknown, not $0 and not
        # "at least $0".
        return None, False
    return spending.get("month"), spending.get("window_complete") is not True
