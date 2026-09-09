"""Pure helpers for what the home screen shows.

Kept out of `main.py` deliberately: `main.py` imports psycopg and FastAPI, so
anything defined there can only be tested with a database driver installed.
These functions are the two places the dashboard was reporting things it did
not know, so they are exactly the parts that need tests that run everywhere.

Nothing here does I/O.
"""
from datetime import datetime, timedelta, timezone


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


def portfolio_state(stocks):
    """`ok`, `unreachable` or `not_configured` — the same three states
    `firefly_state` draws, for the same reason.

    `_get` swallows a timeout and returns `{}`, and `stocks or {"configured":
    False}` turned that into a confident "No holdings yet. Add your stocks →".
    So a blip in the stocks container told you to go and set up a portfolio you
    had already set up. The stocks service answers `{"configured": False}` on
    its own when it genuinely has no holdings, so an EMPTY payload can only
    mean it never answered.
    """
    if not stocks:
        return "unreachable"
    if stocks.get("configured") is False:
        return "not_configured"
    return "ok"


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
#   ok             the schedule service answered AND the calendar integration
#                  is known to be connected
#   unreachable    the schedule service did not answer at all — we have nothing
#   disconnected   the service answered, but the calendar integration has no
#                  credential, so anything living only in Google is missing
#   needs_consent  a credential exists and Google refuses it for Calendar —
#                  wrong scopes. Fixable in one click, and never by waiting
#   unknown        the service answered, but the integration's health cannot be
#                  established — which is NOT the same as healthy
#
# `unknown` exists because the previous two-state version returned `ok` for
# any truthy payload, so a disconnected calendar rendered as a clear week.
# Absence of evidence was being presented as evidence of absence.
#
# `needs_consent` exists for the mirror-image reason. It was previously folded
# into `unknown`, whose caveat says the connection could not be confirmed —
# language that invites you to wait. But a refresh token minted without the
# calendar scope never heals on its own, so "wait" is advice that cannot work,
# and the week grid quietly stays incomplete for as long as you take it.
SCHEDULE_STATES = ("ok", "unreachable", "disconnected", "needs_consent", "unknown")

# The states where Google Calendar is definitely NOT syncing and a person has
# to reconnect the account. Both render an action, not a shrug.
RECONNECT_STATES = ("disconnected", "needs_consent")


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

    # A credential that Calendar refuses. Also conclusive, and also negative —
    # but it points at a different repair than "disconnected" does, so it is
    # reported separately rather than being rounded to the nearest state.
    if evidence.get("state") == "needs_consent":
        return "needs_consent"

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
            # The schedule service stamps `source` on import; anything it
            # pulled out of the real Google Calendar carries
            # 'google_calendar'. Resolved here rather than in the browser so
            # the front end never has to know the service's source strings,
            # and so the calendar sweep covers it.
            "from_google": event.get("source") == "google_calendar",
            "location": event.get("location") or None,
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
                "google": sum(1 for e in entries if e["from_google"]),
            },
        })
        previous_month = day.month

    # A running total of what came out of Google Calendar. This is the only
    # POSITIVE, visible-to-the-user evidence that the import is actually
    # working: `state == "ok"` says the API answered a probe, which it will do
    # just as happily when nothing has ever been imported. A number here that
    # stays at zero on a week you know is busy is the symptom worth seeing.
    from_google = sum(d["counts"]["google"] for d in out)

    return {"days": out, "beyond": beyond,
            "from_google": from_google,
            "spans_months": len({d["month_index"] for d in out}) > 1}


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


# --- the box's own deploy state (PRODUCT_IDEAS #24) --------------------------

def _deploy_age(stamp, now):
    """Seconds between an ISO stamp and `now`, or None.

    None when the stamp is missing or unparseable — never 0. A zero here would
    render as "deployed just now", which is the opposite of "we don't know
    when", and `docs/BUDGETS.md`'s rule that suppressed values are null rather
    than 0 exists because that substitution is always a lie.
    """
    if not stamp or now is None:
        return None
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now.tzinfo)
    # Both sides converted to UTC before subtracting. Python subtracts two
    # aware datetimes that share a tzinfo in WALL CLOCK, so on the day New York
    # skips 02:00 a build deployed at local 00:00 and read at local 06:00 would
    # come back as six hours old when only five hours happened. An age is
    # elapsed real time or it is nothing.
    return (now.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()


def deploy_state(record, now=None):
    """What the box is actually running, from `~/.frankenstein/deployed.json`.

    Four states, and the split that matters is `failed` vs `current`. A deploy
    that fails leaves the previous build serving: `deploy.sh` only advances
    `running_commit` on success, so the box keeps answering requests happily
    while `last_attempt_commit` moves on without it. From the outside that is
    indistinguishable from a healthy deploy — which is how a stale build sits
    there for days while you assume your fix is live.

    That is not hypothetical. On 2026-09-07 a test that read the live repo's
    own git state passed in every worktree and failed on the box, aborting the
    deploy that carried it. Five poll cycles, `1 failed / 2307 passed` each
    time, the box held on the previous commit, and nothing in the UI said so.

      unknown  no record, unreadable, or self-contradictory. The assistant runs
               in a container and the record is written on the host, so an
               absent mount lands here. It must never read as `current`:
               "we cannot see the box" and "the box is up to date" are
               different facts and this is the one card whose whole job is not
               to confuse them.
      pending  a record exists but no successful deploy is confirmed in it.
               Says nothing about whether containers are up.
      failed   the last attempt did not succeed. `running` is an OLDER build
               than `attempted`, and `running` is what you are looking at.
      current  the last attempt succeeded and it is what is running.

    `now` is injected rather than read, per `docs/TESTING.md`: the age this
    returns is the one number here that moves on its own.
    """
    if not isinstance(record, dict) or not record:
        return {"state": "unknown", "running": None, "attempted": None,
                "last_result": None, "age_seconds": None, "attempt_age_seconds": None}

    running = record.get("running_commit") or None
    attempted = record.get("last_attempt_commit") or None
    result = record.get("last_result") or None
    out = {
        "running": running,
        "attempted": attempted,
        "last_result": result,
        "age_seconds": _deploy_age(record.get("last_success_at"), now),
        "attempt_age_seconds": _deploy_age(record.get("last_attempt_at"), now),
    }

    if result is None:
        # A record with no verdict in it tells us nothing about the box.
        return {**out, "state": "unknown"}
    if result != "success":
        # Honest even when running is None: the attempt failed either way.
        return {**out, "state": "failed"}
    if not running:
        return {**out, "state": "pending"}
    if attempted and running != attempted:
        # deploy.sh sets running = attempted on success, so these disagreeing
        # means the record is inconsistent. Report that we don't know rather
        # than picking whichever field flatters the box.
        return {**out, "state": "unknown"}
    return {**out, "state": "current"}

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


# --- choosing what to recommend (PRODUCT_IDEAS #34) --------------------------

def first_undismissed(candidates, hidden=None, fallback=None):
    """The best recommendation you have not waved off.

    Lives here rather than in `main.py` for this file's founding reason: the
    module that generates the candidates imports psycopg and so cannot be
    imported by a test at all, and "which suggestion do you actually get" is
    precisely the decision that needs covering.

    Falling THROUGH is the whole point. A first-match-wins chain that returns
    the top rule can only hide it and show nothing in its place, and "I have
    handled that, tell me the next thing" is the entire reason to be able to
    say handled.

    The fallback is deliberately not dismissible: "You're on track" is the
    absence of a recommendation, and there is nothing behind it to reveal.
    """
    hide = hidden or set()
    for rec in candidates or []:
        if not isinstance(rec, dict):
            continue
        key = rec.get("key")
        # No key means it cannot be identified, so it cannot have been
        # dismissed — showing it is the only safe reading.
        if not key or key not in hide:
            return rec
    return fallback if fallback is not None else {
        "key": None, "title": "You're on track",
        "reason": "Nothing urgent right now — nice.", "action": None}
