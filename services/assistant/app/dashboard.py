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


# --- data safety (SCRUM-67) --------------------------------------------------
#
# "A backup you have never restored is a belief, not a backup."
#
# The number this exists to put on screen is DAYS SINCE THE LAST VERIFIED
# RESTORE, and the states below exist because three different things were
# previously indistinguishable from safety:
#
#   unknown  no record is readable. The assistant runs in a container and the
#            record is written on the host, so an absent mount lands here. It
#            must never read as "fine" — "we cannot see the box" and "the box
#            is safe" are different facts.
#   never    a record exists and no restore has ever succeeded. This is the
#            state the ticket asks for by name, in red, and it is the state
#            every installation starts in.
#   stale    a restore succeeded, but long enough ago that it is a belief
#            again. A proof has an expiry date.
#   ok       verified recently.
#
# `stale` is why this is not just a date. A restore proven once, two years
# ago, against a schema that has changed since, is not evidence about today's
# backup — but it looks like evidence, which is worse than nothing.
RESTORE_STALE_DAYS = 35
BACKUP_STALE_DAYS = 3


def _days_since(stamp, now):
    """Whole LOCAL CALENDAR days from an ISO stamp to `now`, or None.

    None means unknown and never zero: "we could not read the date" rendered
    as "0 days ago" is the most reassuring possible version of no information.

    Calendar days in the reader's zone, not elapsed_seconds // 86400. A drill
    that ran at 23:00 last night is "1 day ago" at breakfast — a person does
    not say "today" about yesterday evening because only nine hours passed —
    and the elapsed form was also off by one across every DST change, since a
    week of 23-hour-or-25-hour days is not 7 * 86400 seconds. Both sides are
    moved into `now`'s zone before the date is taken, so a UTC stamp written
    at 23:30 local cannot land on tomorrow's date and read as -1.

    The future guard is kept, and kept in REAL time: a clock skewed forward on
    the box must read as unknown, not as a restore that happens tomorrow and
    is permanently green.
    """
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=now.tzinfo)
    if (now.astimezone(timezone.utc) - when.astimezone(timezone.utc)).total_seconds() < 0:
        # A future stamp is a broken clock somewhere, not freshness.
        return None
    tz = now.tzinfo
    return (now.astimezone(tz).date() - when.astimezone(tz).date()).days


DISK_LOW_PCT = 10


def disk_state(usage):
    """Free space on the volume the state directory lives on — SCRUM-67 fact 1.

    This needs no privileged host access: the state directory is a bind mount,
    and statvfs through a bind mount reports the underlying host filesystem.
    So `shutil.disk_usage("/var/frankenstein")` inside the container IS the
    OptiPlex's data disk. It is only meaningful when that mount exists, which
    is why main.py passes None rather than a number when the directory is
    absent — the container's own filesystem is not the fact being asked for.

    `usage` is {"total": bytes, "free": bytes} or None. Anything short of two
    sane numbers is `unknown`, never `ok`.
    """
    u = usage if isinstance(usage, dict) else {}
    total, free = u.get("total"), u.get("free")
    if not (isinstance(total, (int, float)) and isinstance(free, (int, float))) \
            or isinstance(total, bool) or isinstance(free, bool) \
            or total <= 0 or free < 0 or free > total:
        return {"state": "unknown", "free_pct": None, "free_bytes": None, "total_bytes": None}
    pct = round(100.0 * free / total, 1)
    return {"state": "low" if pct < DISK_LOW_PCT else "ok", "free_pct": pct,
            "free_bytes": int(free), "total_bytes": int(total)}


def data_safety(record, now):
    """What the home screen may claim about whether this data is recoverable.

    `record` is the host's data-safety.json, written by scripts/backup.sh and
    scripts/restore.sh. `{}` means it could not be read.
    """
    if not isinstance(record, dict) or not record:
        return {"state": "unknown", "restore_days": None, "backup_days": None,
                "restore_kind": None, "backup_stale": None, "rows": None}

    restore_days = _days_since(record.get("last_restore_at"), now)
    backup_days = _days_since(record.get("last_backup_at"), now)

    if record.get("last_restore_at") is None:
        state = "never"
    elif restore_days is None:
        # A record that names a restore but carries an unreadable date tells
        # us nothing about when, which is the only thing being asked.
        state = "unknown"
    elif restore_days > RESTORE_STALE_DAYS:
        state = "stale"
    else:
        state = "ok"

    return {
        "state": state,
        "restore_days": restore_days,
        "restore_kind": record.get("restore_kind"),
        "rows": record.get("restore_rows"),
        "backup_days": backup_days,
        # Reported separately: a fresh backup and a proven restore are two
        # different assurances, and having one says nothing about the other.
        "backup_stale": None if backup_days is None else backup_days > BACKUP_STALE_DAYS,
        "last_backup_result": record.get("last_backup_result"),
        "last_restore_result": record.get("last_restore_result"),
    }


def amex_brief(amex):
    """The Amex credits worth acting on, for the home screen.

    The amex service already did the period arithmetic — this only decides what
    the card leads with, and refuses to fill in blanks it was not given.

    Three states, the same rule as firefly_state and the rest: `{}` means `_get`
    swallowed a timeout, and an unreachable service is NOT a week with nothing
    expiring. That distinction matters more here than almost anywhere else on
    the page, because these credits do not roll over — a quiet card on the 31st
    is indistinguishable from a card that has given up, and one of those costs
    real money at midnight.
    """
    # `{}` is a swallowed timeout; `state: "unreachable"` is the service
    # reaching us to say its own store is down. Same card either way, and both
    # must beat the branch below that prints totals.
    if not amex or amex.get("state") not in (None, "ok"):
        return {"state": "unreachable", "at_risk": None, "available": None,
                "urgent": [], "soonest_days": None, "ytd_net": None,
                "ytd_captured": None, "annual_fees": None}

    rows = amex.get("rows") or []
    urgent = [r for r in rows if r.get("urgent")]
    unused = [r for r in rows if not r.get("used")]
    return {
        "state": "ok",
        # What you lose by doing nothing this week. The headline.
        "at_risk": amex.get("at_risk"),
        "available": amex.get("available"),
        "captured_this_period": amex.get("captured_this_period"),
        # Year to date, against the two annual fees. `null` rather than 0 when
        # the service did not send it: an unreported figure is not break-even,
        # and this is the number that decides whether a card gets renewed.
        "ytd_captured": (amex.get("ytd") or {}).get("captured"),
        "ytd_net": (amex.get("ytd") or {}).get("net"),
        "annual_fees": amex.get("annual_fees"),
        "urgent": [
            {"key": r["key"], "card": r["card"], "name": r["name"],
             "amount": r["amount"], "days_left": r["days_left"],
             "where": r.get("where", ""), "enroll": r.get("enroll", False)}
            for r in urgent
        ],
        # The next deadline even when nothing is urgent yet, so the card can
        # say something true on a quiet day instead of nothing at all.
        "soonest_days": min((r["days_left"] for r in unused), default=None),
        "soonest_name": min(unused, key=lambda r: r["days_left"])["name"] if unused else None,
        "catalogue_checked": amex.get("catalogue_checked"),
    }


def _to_float(value):
    """A number, or None. Firefly sends balances as STRINGS ("-71.17"), and a
    string that will not parse is unknown — never zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def card_debt(firefly):
    """What is owed across Firefly's liability accounts.

    Anthony, 2026-09-16, listing balances by hand so the dashboard would be
    accurate: $71.17 on Discover, $56.58 on the Amex Gold. Those are live
    figures, so they are read from the ledger rather than written down here —
    a number typed into this file is right for a day and wrong afterwards,
    which is the failure docs/BUDGETS.md is about.

    FOUR STATES, because three of them are not "you owe nothing":

      unreachable      Firefly did not answer, or answered without a
                       liabilities list. Total is null.
      no_liabilities   Firefly answered and has NO liability accounts. This is
                       NOT $0 owed. It is the state you are in when cards are
                       entered as asset accounts — which runway.py records as
                       the known-bad setup — and reporting it as zero debt is
                       the most reassuring possible reading of a ledger that
                       has not been told about the cards.
      ok               Real liability accounts, with balances.
      ok + partial     Some balance would not parse. The total is the sum of
                       the ones that did, and `partial` says so, because a
                       total that quietly drops a card UNDERSTATES debt.

    A liability you have overpaid has a positive balance and owes nothing; it
    contributes 0 rather than a negative, so a credit on one card cannot mask
    what is owed on another.
    """
    if not firefly or not firefly.get("connected"):
        return {"state": "unreachable", "total": None, "cards": [], "partial": False}

    liabilities = firefly.get("liabilities")
    if liabilities is None or not isinstance(liabilities, list):
        return {"state": "unreachable", "total": None, "cards": [], "partial": False}
    if not liabilities:
        return {"state": "no_liabilities", "total": None, "cards": [], "partial": False}

    cards, total, partial = [], 0.0, False
    for a in liabilities:
        if not isinstance(a, dict):
            partial = True
            continue
        name = a.get("name") or "(unnamed)"
        balance = _to_float(a.get("balance"))
        if balance is None:
            partial = True
            cards.append({"name": name, "owed": None, "balance": None})
            continue
        # Firefly carries a debt as a NEGATIVE balance. Overpaid is positive
        # and owes nothing.
        owed = round(-balance, 2) if balance < 0 else 0.0
        total += owed
        cards.append({"name": name, "owed": owed, "balance": round(balance, 2)})

    # Biggest debt first; the ones we could not read last, where they are
    # visible rather than buried among the zeroes.
    cards.sort(key=lambda c: (c["owed"] is None, -(c["owed"] or 0)))
    return {"state": "ok", "total": round(total, 2), "cards": cards,
            "partial": partial}


def on_google_calendar(event):
    """Is this event actually ON the Google Calendar?

    Anthony, 2026-09-16: "i want REPEATS OFF my calendar. it should look
    exactly like my google calendar." So the dashboard calendar mirrors Google
    and shows nothing else. Two ways an event qualifies, and they are different
    facts:

      source == 'google_calendar'   `sync_from_calendar` imported it FROM
                                    Google. It is on the calendar because that
                                    is where it came from.
      gcal_event_id is set          this app created it and the push to Google
                                    SUCCEEDED, so Google has it too. The id is
                                    the receipt — it is only written after the
                                    API call returns one.

    Anything else is a row that exists here and nowhere else: a hold from a
    proposed interview time, a manual event added while Calendar was
    unreachable, a leftover from a sync that half-finished. Those are the
    repeats.

    WHY THE RECEIPT AND NOT THE SOURCE. A confirmed interview this app booked
    is `source='gmail'`, and it IS on the calendar — `gcal.list_upcoming` skips
    re-importing it precisely because it carries our own marker. Filtering on
    source alone would delete real appointments off the dashboard while leaving
    the duplicates it was written to remove.

    WHEN CALENDAR IS NOT CONNECTED this returns False for everything, and the
    calendar renders empty. That is the honest reading of "show me what Google
    has" when the answer is "we cannot see Google" — and `schedule_state`
    already reports the connection separately, so the card says which.
    """
    if not isinstance(event, dict):
        return False
    if event.get("source") == "google_calendar":
        return True
    return bool(event.get("gcal_event_id"))


def weather_brief(weather):
    """Current conditions for the home screen, and nothing invented.

    The service already parsed Open-Meteo and did the hour arithmetic; this
    only decides what the card carries and passes its three states through
    unchanged. `not_configured` (nobody has picked a place) and `unreachable`
    (we asked and got nothing) are different sentences and the card says which
    — the first is a thing you can fix in ten seconds and the second is not.

    The one number a person takes from a weather card without reading it is the
    big one, so a temperature is `null` unless the service sent it. A card
    showing 0° because a request timed out is believable in February.
    """
    if not weather:
        return {"state": "unreachable", "place": None, "temp": None,
                "high": None, "low": None, "hourly": [], "degree": None}

    state = weather.get("state")
    if state != "ok":
        return {"state": state if state in ("not_configured", "unreachable")
                else "unreachable",
                "place": (weather.get("place") or {}).get("name"),
                "temp": None, "high": None, "low": None, "hourly": [],
                "degree": weather.get("degree")}

    cur = weather.get("current") or {}
    return {
        "state": "ok",
        "place": (weather.get("place") or {}).get("name"),
        "temp": cur.get("temp"),
        "feels_like": cur.get("feels_like"),
        "label": cur.get("label"),
        "glyph": cur.get("glyph"),
        "severity": cur.get("severity"),
        # Whether the reading is old enough that calling it "now" would be a
        # stretch. The service decides; this only carries the verdict.
        "stale": cur.get("stale"),
        "high": weather.get("high"),
        "low": weather.get("low"),
        # The rest of today, by the hour. See `rest_of_today`.
        "hourly": rest_of_today(weather.get("hourly") or []),
        "degree": weather.get("degree"),
        # The next day worth warning about: rain, snow, a storm. Nothing to
        # say is a real and common answer, and the card says nothing then
        # rather than manufacturing a headline.
        "next_rough": _next_rough_day(weather.get("daily") or []),
    }


# The header pill shows the hours left in the day. Two bounds, because "the
# rest of today" is a number that swings from 23 to 0 depending on when you
# look, and a header cannot.
HOURS_MAX = 12     # the whole of what the service sends; CSS sheds the far
                   # end as the window narrows, so this is a ceiling rather
                   # than a layout decision
HOURS_MIN = 4      # and shorter than this stops being worth the space


def rest_of_today(hourly):
    """The remaining hours of today, bounded so the header stays a header.

    Anthony, 2026-09-18: "it can be slightly longer showing the rest of the
    days weather (like by the hour or whatever)."

    Taken literally that is 23 chips at 1am and none at 11pm, so:

      * hours on the same calendar date as the first entry — "the rest of
        today", which is what was asked for and what it is nearly all day;
      * but never fewer than HOURS_MIN, rolling past midnight into tomorrow
        rather than showing a stub at 10pm. Late evening is exactly when the
        next few hours are worth seeing, and a strip that empties out as the
        day ends is worst precisely when it matters;
      * and never more than HOURS_MAX, because a header is a glance.

    Dates come from each row's `time` ("2026-09-18T01:00"), which the weather
    service already emits; a row whose time will not parse keeps its place
    rather than truncating the strip at the first bad entry.
    """
    rows = [r for r in hourly if isinstance(r, dict)]
    if not rows:
        return []

    def day_of(row):
        text = row.get("time")
        return text[:10] if isinstance(text, str) and len(text) >= 10 else None

    first_day = day_of(rows[0])
    if first_day is None:
        return rows[:HOURS_MAX]

    today = [r for r in rows if day_of(r) in (first_day, None)]
    # `rows` is ordered, so extending past today is just taking more of it.
    chosen = today if len(today) >= HOURS_MIN else rows[:HOURS_MIN]
    return chosen[:HOURS_MAX]


def _next_rough_day(daily):
    """The soonest upcoming day whose weather should change a plan.

    Severity comes from the WMO table in the weather service, where 0 is
    benign. Today is included — rain this afternoon is exactly the thing you
    want to be told before you leave.
    """
    rough = [d for d in daily if (d.get("severity") or 0) >= 2]
    if not rough:
        return None
    first = rough[0]
    return {"date": first.get("date"), "weekday": first.get("weekday"),
            "label": first.get("label"), "is_today": first.get("is_today")}


def resale_state(resale):
    """`ok`, `unreachable` or `not_configured` — the same three states, for the
    same reason, as firefly_state and portfolio_state.

    The PowerBuy service answers `mode: "disconnected"` when it holds no
    credentials, and `_get` swallows a timeout into `{}`. Those are different
    facts: one means "there is nothing to show you", the other means "we could
    not look". A resale card that renders an outage as $0 expected and 0
    expiring is telling you the most reassuring possible version of a thing it
    does not know — and this is the card whose whole job is to say when money
    is about to be lost.
    """
    if not resale:
        return "unreachable"
    if resale.get("mode") == "disconnected":
        return "not_configured"
    return "ok"


def resale_brief(resale):
    """What the home screen shows about the resale book.

    `expiring` leads when it is non-zero: of the four figures PowerBuy tracks
    it is the only one carrying a deadline, and a deadline is the only reason
    a number belongs on a screen you glance at rather than in the sub-app.

    Every figure is None rather than 0 when the service could not be reached,
    per docs/BUDGETS.md: a suppressed value is null, never zero. Nothing here
    is invented — the fields come straight from powerbuy.summarize().
    """
    state = resale_state(resale)
    summary = (resale or {}).get("summary") or {}
    if state != "ok":
        return {"state": state, "profit": None, "unpaid": None,
                "expiring": None, "in_flight": None, "total": None,
                "urgent": False}
    def _int(key):
        value = summary.get(key)
        return None if value is None else int(value)
    expiring = _int("expiring_soon_count")
    return {
        "state": state,
        "profit": summary.get("expected_profit"),
        "unpaid": _int("unpaid_count"),
        "expiring": expiring,
        "in_flight": _int("not_delivered_count"),
        "total": _int("total_purchases"),
        # The one thing on this card that is time-critical, and so the one
        # thing allowed to shout.
        "urgent": bool(expiring),
    }


def portfolio_state(stocks):
    """`ok`, `unreachable` or `not_configured` — PRODUCT_IDEAS #13, and the
    same three states `firefly_state` draws, for the same reason.

    `_get` swallows a timeout and returns `{}`, and the home payload used to
    collapse that into `{"configured": False}`. So a stocks container that was
    briefly down told you **"No holdings yet. Add your stocks →"** — an
    instruction to go and repair configuration that was already correct, and
    acting on it means hunting a problem that does not exist. The stocks
    service answers `{"configured": False}` itself when it genuinely has no
    holdings, so an EMPTY payload can only mean it never answered.
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
#   not_configured this deployment has no FC_INTERNAL_SECRET, so the schedule
#                   service cannot borrow the Google credential at all. Fixed
#                   by setting one value in .env — never by waiting, and never
#                   by reconnecting Google (SCRUM-114)
SCHEDULE_STATES = ("ok", "unreachable", "disconnected", "needs_consent",
                   "not_configured", "unknown")

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

    # A missing shared secret is a configuration fact, not a Google fact. It
    # must not fold into `unknown`, whose caveat invites waiting for something
    # that will never happen on its own.
    if evidence.get("state") == "not_configured":
        return "not_configured"

    # Everything else: a failed probe, no probe, or gmail reporting "live"
    # while its own sync_status is failed/never. None of those establish
    # health, and a clear week that might not be clear is the one thing this
    # function exists to prevent.
    return "unknown"



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

    Orthogonal to all four: `tests` and `untested` say whether the SERVING
    build was gated by the suite. A `current` deploy can still be untested if
    it went out under DEPLOY_SKIP_TESTS=1, and that stays true until a tested
    deploy replaces it — which is the whole point of recording it (SCRUM-108).
    `tests` of None is unknown, never passed.

    `now` is injected rather than read, per `docs/TESTING.md`: the age this
    returns is the one number here that moves on its own.
    """
    if not isinstance(record, dict) or not record:
        return {"state": "unknown", "running": None, "attempted": None,
                "last_result": None, "tests": None, "untested": False,
                "age_seconds": None, "attempt_age_seconds": None}

    running = record.get("running_commit") or None
    attempted = record.get("last_attempt_commit") or None
    result = record.get("last_result") or None
    # What gated the build that is SERVING — "passed", "skipped", or None.
    #
    # None means UNKNOWN, not passed (SCRUM-108). A record written before the
    # test verdict was recorded has no such key, and so does one written by a
    # deploy.sh that lost the field again. Reporting "passed" from an absent
    # key is the exact failure `docs/BUDGETS.md` bans in the money layer, for
    # the same reason: it invents reassurance out of missing data.
    running_tests = record.get("running_tests") or None
    out = {
        "running": running,
        "attempted": attempted,
        "last_result": result,
        "tests": running_tests,
        # The one flag a card can render without interpreting the rest: the
        # code now serving went out with the gate switched off.
        "untested": running_tests == "skipped",
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


# --- is the import actually running, and landing? (SCRUM-142) ----------------

IMPORT_ATTEMPT_STALE_DAYS = 2   # the cron is daily; two missed days is news
IMPORT_EMPTY_SUSPECT_DAYS = 3   # importer runs, ledger unmoved this long: look


def import_state(record, ledger_ingest_days=None, now=None):
    """What the home screen may say about the scheduled Firefly import.

    Two signals that only mean something together. The import record says
    whether scripts/firefly-import.sh ran and what the importer did; the
    ledger's own `ingest_days` (from transaction created_at, computed by the
    firefly service) says whether data has been entering. Each alone is
    ambiguous — and the ambiguous case is precisely the one worth catching:

      never       nothing has ever triggered an import from here. The cron is
                  not set up. Loud, because until it is, "importing daily"
                  is a hope.
      stale       it used to run and has not for two days. The cron stopped.
      failed      it ran and the importer refused or could not be reached.
      unverified  it ran, the importer answered, and the firefly service
                  could not say whether anything entered.
      quiet       it ran, the importer answered, nothing entered — and the
                  ledger moved recently anyway. A day with no bank activity.
      suspect     it ran, nothing entered, and the ledger has been still for
                  days. The importer is running and delivering nothing:
                  the SCRUM-40 shape, and the one sentence neither signal
                  can say alone.
      ok          it ran and rows entered.
      unknown     no readable record. Not the same as fine.
    """
    base = {"state": "unknown", "attempt_days": None, "landed_days": None,
            "last_result": None, "reason": None, "rows": None, "kind": None,
            "ledger_ingest_days": ledger_ingest_days}
    if not isinstance(record, dict) or not record:
        return base
    attempt = record.get("last_import_attempt_at")
    if attempt is None:
        return {**base, "state": "never"}
    attempt_days = _days_since(attempt, now)
    out = {**base,
           "attempt_days": attempt_days,
           "landed_days": _days_since(record.get("last_import_at"), now),
           "last_result": record.get("last_import_result"),
           "reason": record.get("last_import_reason"),
           "rows": record.get("import_rows"),
           "kind": record.get("import_kind")}
    if attempt_days is None:
        return out                                   # unreadable stamp
    result = out["last_result"]
    if attempt_days > IMPORT_ATTEMPT_STALE_DAYS:
        out["state"] = "stale"
    elif result == "failed":
        out["state"] = "failed"
    elif result == "unverified":
        out["state"] = "unverified"
    elif result == "empty":
        idle = ledger_ingest_days
        out["state"] = ("suspect" if isinstance(idle, int) and not isinstance(idle, bool)
                        and idle >= IMPORT_EMPTY_SUSPECT_DAYS else "quiet")
    elif result == "ok":
        out["state"] = "ok"
    return out
