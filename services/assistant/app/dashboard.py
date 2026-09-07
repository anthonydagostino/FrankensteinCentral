"""Pure helpers for what the home screen shows.

Kept out of `main.py` deliberately: `main.py` imports psycopg and FastAPI, so
anything defined there can only be tested with a database driver installed.
These functions are the two places the dashboard was reporting things it did
not know, so they are exactly the parts that need tests that run everywhere.

Nothing here does I/O.
"""
from datetime import datetime, timedelta


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


# --- the seven-day week window ----------------------------------------------
#
# The dashboard's schedule card used to be one flat list of the next six
# events. That answers "what is next" but not "what does my week look like",
# and it silently rendered an unreachable schedule service as a calm empty day.
# Everything below is pure and takes `now`/`local_tz` from the caller, so the
# calendar sweep in the tests can pin any date it likes — per docs/TESTING.md,
# none of this is ever exercised against the real "today".

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday")

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")

# One decorative theme key per month, consumed by CSS only. The key never
# carries meaning about the data — it is picked from the date and nothing else.
SEASON_KEYS = ("jan", "feb", "mar", "apr", "may", "jun",
               "jul", "aug", "sep", "oct", "nov", "dec")

WINDOW_DAYS = 7


def ordinal_suffix(day):
    """`st`/`nd`/`rd`/`th` for a day of the month.

    The 11/12/13 branch is the whole reason this is not `day % 10`: the
    eleventh is the 11th, not the 11st, and the same holds for 12 and 13.
    """
    if 11 <= (day % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def ordinal(day):
    return f"{day}{ordinal_suffix(day)}"


# What the week card is allowed to claim about where its events came from.
#
#   ok            the schedule service answered AND the calendar integration
#                 is known to be connected
#   unreachable   the schedule service did not answer at all — we have nothing
#   disconnected  the service answered, but the calendar integration has no
#                 credential, so anything living only in Google is missing
#   unknown       the service answered, but the integration's health cannot be
#                 established — which is NOT the same as healthy
#
# `unknown` exists because the previous two-state version returned `ok` for
# any truthy payload, so a disconnected calendar rendered as a clear week.
# Absence of evidence was being presented as evidence of absence.
SCHEDULE_STATES = ("ok", "unreachable", "disconnected", "unknown")


def schedule_state(schedule, calendar_link=None, calendar_evidence=None):
    """Where the week's events came from, and how much to trust the gaps.

    `schedule` is the schedule service's payload; `{}` means `_get` swallowed
    a timeout, exactly as it does for Firefly.

    `calendar_link` is the gmail service's payload, used ONLY as negative
    evidence. An earlier version of this function read gmail's `mode == "live"`
    as proof the Calendar integration was healthy. It is not, for two separate
    reasons:

      * `services/gmail/app/main.py` deliberately KEEPS `mode="live"` when an
        inbox fetch fails but cached items exist, setting
        `sync_status="failed"` alongside it. "live" there means "still serving
        last-known-good mail", not "the connection works".
      * Even a genuinely healthy Gmail sync says nothing about Calendar.
        `gcal.py` borrows gmail's token, but a shared credential is not proof
        that the Calendar API is reachable, in scope, or actually syncing.

    So `ok` is reserved for POSITIVE, Calendar-specific evidence, supplied by
    the caller as `calendar_evidence`. `GET /events` cannot supply it: it
    returns local Postgres rows and answers identically whether Google is
    connected or not. The schedule service's read-only `GET /calendar-health`
    can, and `main.py` fetches it in the same gather as everything else — it
    is the only call on this path that actually talks to Calendar. When that
    probe is absent or failed, the answer is `unknown`, which is the honest
    answer rather than a comfortable one.

    The one thing gmail CAN establish is the absence of a credential:
    `mode == "disconnected"` means no OAuth consent exists at all, so Calendar
    cannot be syncing either. That is sound negative evidence.

    Local commitments stay real in every state: the rows in Postgres were
    stored whatever Google is doing, so the caller still renders them.
    """
    if not schedule:
        return "unreachable"

    evidence = calendar_evidence if isinstance(calendar_evidence, dict) else {}
    link = calendar_link or {}

    # Positive Calendar evidence is the ONLY route to "ok" — something that
    # actually performed a Calendar read said so.
    if evidence.get("ok") is True:
        return "ok"

    # No credential at all, from either side. Conclusive negative evidence.
    if evidence.get("state") == "disconnected" or link.get("mode") == "disconnected":
        return "disconnected"

    # Everything else: a failed probe, no probe, or gmail reporting "live"
    # while its own sync_status is failed/never. None of those establish
    # health, and a clear week that might not be clear is the one thing this
    # function exists to prevent.
    return "unknown"


def portfolio_state(stocks):
    """`ok`, `unreachable` or `not_configured` — PRODUCT_IDEAS #13.

    `_get` swallows a timeout and returns `{}`, and the home payload used to
    collapse that into `{"configured": False}`. So a stocks container that was
    briefly down told you **"No holdings yet. Add your stocks →"** — an
    instruction to go and fix configuration that was already correct. Acting on
    it means hunting a problem that does not exist.

    Exactly the same three-states-not-two rule as `firefly_state`, which is the
    pattern this repo already got right, applied to the one card the idea's
    acceptance signal names.
    """
    if not stocks:
        return "unreachable"
    if stocks.get("configured") is False:
        return "not_configured"
    return "ok"


def _event_bounds(event, local_tz):
    """(start, end, all_day) in local time, or None when unparseable.

    A date-only timestamp ("2026-10-31") is an all-day event: it has no clock
    time to show, and rendering it as midnight would file a Halloween all-dayer
    under "Morning" alongside a 7am alarm.
    """
    raw_start = event.get("starts_at") or event.get("start")
    start = parse_event_dt(raw_start, local_tz)
    if start is None:
        return None
    all_day = "T" not in str(raw_start) and " " not in str(raw_start).strip()
    end = parse_event_dt(event.get("ends_at"), local_tz) or start
    if end < start:
        end = start
    return start, end, all_day


def _slot_for(dt, all_day):
    if all_day:
        return "allday"
    hour = dt.hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _time_label(dt, all_day):
    """`9 AM`, `9:30 PM`, `All day`. The `:00` is dropped on the hour."""
    if all_day:
        return "All day"
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    return f"{hour} {suffix}" if dt.minute == 0 else f"{hour}:{dt.minute:02d} {suffix}"


def _mark_conflicts(entries):
    """Flag every entry whose time overlaps another one's.

    Two commitments in the same hour is precisely the "approaching conflict"
    the product vision asks the hub to surface, and it is invisible in a flat
    list. All-day events do not conflict with timed ones — an all-day marker
    is context, not a competing obligation.

    This runs over the WHOLE window, not one day at a time. A 23:30 call and
    a 00:15 call are a real collision even though the calendar files them
    under different dates, and a per-day pass is structurally blind to it.
    """
    timed = [e for e in entries if not e["all_day"]]
    for a_index, a in enumerate(timed):
        for b in timed[a_index + 1:]:
            # Identical starts collide even when both are zero-length, which a
            # plain interval test would miss.
            hit = (a["_start"] == b["_start"] or
                   (a["_start"] < b["_end"] and b["_start"] < a["_end"]))
            if hit:
                a["conflict"] = True
                b["conflict"] = True
                # Named so the column can say which neighbour it clashes
                # with when that neighbour is not in the same column. The
                # direction matters: telling someone a 00:15 call "overlaps
                # next day" when the other half is the night before is worse
                # than saying nothing.
                if a["_start"].date() != b["_start"].date():
                    earlier, later = ((a, b) if a["_start"] <= b["_start"]
                                      else (b, a))
                    earlier["conflict_offday"] = True
                    later["conflict_offday"] = True
                    earlier["conflict_neighbour"] = "next"
                    later["conflict_neighbour"] = "previous"


def week_window(events, now, local_tz, days=WINDOW_DAYS):
    """Today plus the next `days - 1` local days, each with its own events.

    Bucketing is done on local *dates*, never by adding 24-hour offsets to a
    timestamp: a 23-hour or 25-hour DST day would slide events into the
    neighbouring column, which is the same class of bug docs/TESTING.md was
    written about.

    An event already in progress stays on today rather than disappearing
    backwards off the grid, and anything past the last day is counted in
    `beyond` instead of being silently dropped.
    """
    today = now.astimezone(local_tz).date()
    window = [today + timedelta(days=offset) for offset in range(days)]
    index = {day: [] for day in window}
    last_day = window[-1]
    beyond = 0

    for event in events or []:
        if event.get("status", "confirmed") == "declined":
            continue
        bounds = _event_bounds(event, local_tz)
        if bounds is None:
            continue
        start, end, all_day = bounds
        if end < now and not all_day:
            continue  # already finished
        day = start.date()
        ongoing = day < today
        if ongoing:
            # Started earlier, still running: it belongs to today's column.
            if end.date() < today:
                continue
            day = today
        if day > last_day:
            beyond += 1
            continue
        if day not in index:
            continue
        index[day].append({
            **event,
            "time_label": _time_label(start, all_day),
            "end_label": None if all_day or end == start else _time_label(end, all_day),
            "slot": _slot_for(start, all_day),
            "all_day": all_day,
            "ongoing": ongoing or (start <= now <= end and not all_day),
            "needs_you": event.get("status") == "countered",
            "conflict": False,
            "conflict_offday": False,
            "conflict_neighbour": None,
            "_start": start,
            "_end": end,
        })

    # One pass over every placed event, so an overlap that straddles midnight
    # is caught. Must happen before the per-day sort strips the bounds.
    _mark_conflicts([e for day in window for e in index[day]])

    out = []
    previous_month = None
    for offset, day in enumerate(window):
        entries = sorted(index[day], key=lambda e: (not e["all_day"], e["_start"]))
        conflicts = sum(1 for e in entries if e["conflict"])
        statuses = [e.get("status", "confirmed") for e in entries]
        for entry in entries:
            del entry["_start"]
            del entry["_end"]
        out.append({
            "iso": day.isoformat(),
            "weekday": WEEKDAY_NAMES[day.weekday()],
            "weekday_short": WEEKDAY_NAMES[day.weekday()][:3],
            "month": MONTH_NAMES[day.month - 1],
            "month_short": MONTH_NAMES[day.month - 1][:3],
            "month_index": day.month,
            "day": day.day,
            "ordinal": ordinal(day.day),
            "ordinal_suffix": ordinal_suffix(day.day),
            "year": day.year,
            "season": SEASON_KEYS[day.month - 1],
            "is_today": offset == 0,
            "is_tomorrow": offset == 1,
            "is_weekend": day.weekday() >= 5,
            # True on the first card and wherever the window crosses into a new
            # month, so a week spanning Oct/Nov says so instead of restarting
            # its day numbers with no explanation.
            "starts_month": previous_month is None or previous_month != day.month,
            "relative_label": "Today" if offset == 0 else ("Tomorrow" if offset == 1 else ""),
            "long_label": (f"{WEEKDAY_NAMES[day.weekday()]}, {MONTH_NAMES[day.month - 1]} "
                           f"{ordinal(day.day)}, {day.year}"),
            "events": entries,
            "conflicts": conflicts,
            "counts": {
                "total": len(entries),
                "confirmed": statuses.count("confirmed"),
                "pending": statuses.count("pending"),
                "countered": statuses.count("countered"),
                "needs_you": statuses.count("countered"),
            },
        })
        previous_month = day.month

    return {"days": out, "beyond": beyond, "spans_months": len({d["month_index"] for d in out}) > 1}


# --- the weekly review (PRODUCT_IDEAS #5) -----------------------------------
#
# `core` has exposed GET /weekly-review since it was written and nothing in
# gateway/static ever referenced it: a finished feature with no front door.
#
# The timing rule is date-dependent, so it lives here behind the same injected
# clock the rest of this module uses and is swept across a calendar in the
# tests, never run against "today".

# "Sunday evening the home screen leads with the week." Evening starts at 5pm.
WEEKLY_REVIEW_LEAD_HOUR = 17
SUNDAY = 6  # datetime.weekday(): Monday is 0


def weekly_review_slot(now, local_tz):
    """`lead` on Sunday evening, `normal` the rest of the week.

    The review is worth seeing any day — it is the only thing that answers
    "where did the week go" — so it is never hidden. What changes is whether
    it takes the top of the page.
    """
    local = now.astimezone(local_tz)
    if local.weekday() == SUNDAY and local.hour >= WEEKLY_REVIEW_LEAD_HOUR:
        return "lead"
    return "normal"


def weekly_progress(value, goal):
    """(value, goal, pct, state) for one weekly row.

    `state` is `no_goal` when no goal is set, and the percentage is None —
    never 0%. Reporting "0% of 0" as failure is the bug docs/BUDGETS.md's
    honesty rules exist to prevent, in a different costume: not setting a
    goal is not missing one.

    `unknown` covers a missing figure, which is likewise not zero.
    """
    if value is None:
        return {"value": None, "goal": goal, "pct": None, "state": "unknown"}
    if not goal:
        return {"value": value, "goal": None, "pct": None, "state": "no_goal"}
    pct = int(round((value / goal) * 100))
    return {
        "value": value, "goal": goal, "pct": pct,
        "state": "hit" if value >= goal else "under",
    }


def weekly_review(review, now, local_tz):
    """The home screen's slice of `core`'s weekly review.

    Returns None when core did not answer — an absent review is not a week of
    zeroes, and the card is simply not rendered rather than inventing one.
    """
    if not review:
        return None

    study = review.get("study") or {}
    gym = review.get("gym") or {}
    water = review.get("water") or {}

    study_row = weekly_progress(study.get("week_min"), study.get("goal_min"))
    # Last week is context, not a goal: it only earns a trend when both
    # figures exist.
    last = study.get("last_min")
    trend = None
    if study_row["value"] is not None and last is not None:
        trend = study_row["value"] - last

    return {
        "slot": weekly_review_slot(now, local_tz),
        "study": {**study_row, "last_min": last, "trend_min": trend},
        "gym": weekly_progress(gym.get("week"), gym.get("goal")),
        "water": weekly_progress(water.get("days_hit"), water.get("of")),
    }

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

