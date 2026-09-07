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


# ── "since you last checked", computed once and shared across devices ──────
#
# `home.js` kept this in localStorage under `cc_snap`. On one machine it reads
# as a nice touch; across a phone, two MacBooks, a Kali laptop and the OptiPlex
# it means every device tells a different story about what changed, and a fresh
# browser tells none. The baseline belongs to the PERSON, so it lives in core
# and the diff is computed here — where it can be tested at any clock value
# rather than only in a browser.

SINCE_MIN_GAP_MINUTES = 15


def since_snapshot(home):
    """A small, stable fingerprint of a home payload.

    Deliberately not the whole payload: this is stored and compared on every
    load, and a fingerprint that changes whenever any nested figure moves would
    report "something changed" constantly and mean nothing.
    """
    home = home or {}
    inbox = (home.get("inbox") or {}).get("items") or []
    money = home.get("money") or {}
    portfolio = home.get("portfolio") or {}
    score = home.get("score") or {}
    budget = home.get("budget") or {}
    # Interview holds are the pipeline's actual state: Bones proposes slots,
    # they counter, you confirm. A status moving is the thing worth surfacing.
    holds = {}
    for e in home.get("calendar") or []:
        if not isinstance(e, dict):
            continue
        key = e.get("id") or e.get("external_id") or e.get("title")
        status = e.get("status")
        if key and status in ("pending", "countered"):
            holds[str(key)] = status
    return {
        "important_email_ids": sorted(
            str(i.get("id")) for i in inbox
            if isinstance(i, dict) and i.get("important") and i.get("id") is not None),
        "spend_today": money.get("today"),
        "spend_month": money.get("month"),
        "portfolio_value": portfolio.get("value"),
        "score": score.get("score"),
        "holds": holds,
        "over_budget": sorted(budget.get("over_budget") or []),
    }


def _minutes_between(earlier, later):
    if not earlier or not later:
        return None
    return (later - earlier).total_seconds() / 60.0


def since_changes(previous, current, seen_at, now,
                  min_gap_minutes=SINCE_MIN_GAP_MINUTES):
    """What has changed since the user was last shown the dashboard.

    THREE STATES, not two, and `show` and `store` are SEPARATE decisions —
    conflating them deadlocks the whole feature:

      never_seen  show=False, store=True.  There is no baseline, so there is
                  nothing to diff, and inventing an empty one would report
                  every email you already read as new. But the baseline MUST be
                  recorded now, or it never is: an earlier cut of this only
                  stored on a successful show, so the first load never
                  established a baseline, every later load was also
                  "never_seen", and the feature could not start at all.
      too_soon    show=False, store=False. A refresh two minutes after looking
                  should not announce that nothing happened — and must not
                  overwrite the baseline either, or an idle tab refreshing in
                  the background quietly consumes this morning's changes.
      ok          show=True,  store=True.
    """
    gap = _minutes_between(seen_at, now)
    if not isinstance(previous, dict) or not previous or seen_at is None:
        return {"show": False, "store": True, "reason": "never_seen",
                "gap_minutes": gap, "changes": []}
    if gap is not None and gap < min_gap_minutes:
        return {"show": False, "store": False, "reason": "too_soon",
                "gap_minutes": gap, "changes": []}

    changes = []
    prev_ids = set(previous.get("important_email_ids") or [])
    now_ids = set(current.get("important_email_ids") or [])
    new_mail = len(now_ids - prev_ids)
    if new_mail:
        changes.append({
            "key": "email", "icon": "✉️",
            "text": f"{new_mail} new important email{'s' if new_mail > 1 else ''}"})

    # Pipeline stage changes: a slot they countered needs your reply, and a
    # newly proposed one means Bones acted while you were away.
    prev_holds = previous.get("holds") or {}
    now_holds = current.get("holds") or {}
    countered = [k for k, v in now_holds.items()
                 if v == "countered" and prev_holds.get(k) != "countered"]
    proposed = [k for k in now_holds if k not in prev_holds
                and now_holds[k] == "pending"]
    if countered:
        changes.append({
            "key": "countered", "icon": "🟠", "urgent": True,
            "text": f"{len(countered)} interview slot"
                    f"{'s' if len(countered) > 1 else ''} countered — needs your reply"})
    if proposed:
        changes.append({
            "key": "proposed", "icon": "🟡",
            "text": f"{len(proposed)} new slot{'s' if len(proposed) > 1 else ''} proposed"})

    spent = _delta(previous.get("spend_today"), current.get("spend_today"))
    if spent and spent > 0:
        changes.append({"key": "spend", "icon": "💳",
                        "text": f"${round(spent):,} new spending"})

    # A budget crossing INTO over is news; one that was already over is not.
    newly_over = [b for b in (current.get("over_budget") or [])
                  if b not in (previous.get("over_budget") or [])]
    if newly_over:
        changes.append({
            "key": "budget", "icon": "⚠️", "urgent": True,
            "text": f"{', '.join(newly_over[:3])} went over budget"})

    moved = _delta(previous.get("portfolio_value"), current.get("portfolio_value"))
    if moved is not None and abs(moved) >= 1:
        changes.append({
            "key": "portfolio", "icon": "📈",
            "text": f"Portfolio {'up' if moved > 0 else 'down'} ${abs(round(moved)):,}"})

    # A score that has become unknown is not a score that fell.
    ps, cs = previous.get("score"), current.get("score")
    if isinstance(ps, (int, float)) and isinstance(cs, (int, float)) and ps != cs:
        changes.append({"key": "score", "icon": "🎯",
                        "text": f"Score {'up' if cs > ps else 'down'} to {cs}"})

    return {"show": True, "store": True, "reason": "ok",
            "gap_minutes": gap, "changes": changes}


def _delta(before, after):
    try:
        return float(after) - float(before)
    except (TypeError, ValueError):
        return None
