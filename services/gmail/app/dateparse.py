"""Free-text availability parsing: turns "I'm available Mon at 2pm or Tue
10am" into concrete datetimes, and classifies reply language as a
confirmation, a decline, or a counter-proposal.

Lives inside the gmail service (not shared with the assistant) because
thread analysis needs the raw message text right where the Gmail API calls
already happen — sub-apps are independent services with their own containers,
so this is a self-contained duplicate of the same idea in
assistant/app/orchestrator.py, not an import of it.
"""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# The box runs in UTC; "Monday" has to resolve against the user's actual day.
EASTERN = ZoneInfo("America/New_York")

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2,
    "wed": 2, "weds": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

_DATE_RE = re.compile(
    r"\b(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?P<day>\d{1,2})\b",
    re.IGNORECASE,
)
_WEEKDAY_RE = re.compile(
    r"\b(?P<next>next\s+)?(?P<day>mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday|s)?|"
    r"thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b",
    re.IGNORECASE,
)
_RELATIVE_RE = re.compile(r"\b(today|tomorrow)\b", re.IGNORECASE)
_TIME_RE = re.compile(
    r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm|a\.m\.|p\.m\.)\b", re.IGNORECASE
)
_VAGUE_TIME = re.compile(r"\b(morning|afternoon|evening|noon)\b", re.IGNORECASE)
_VAGUE_DEFAULTS = {"morning": (9, 0), "afternoon": (14, 0), "evening": (18, 0), "noon": (12, 0)}

# The start of quoted/forwarded history in a reply — without cutting here,
# every message in a growing thread re-includes every prior message's text,
# so old proposals (and their dates, often stretching a year back by the
# time a thread is old) get re-matched as if they were new every single sync.
_QUOTE_MARKERS = re.compile(
    r"\bOn\s.{0,100}\swrote:|"
    r"^>|\n>|"
    r"-{2,}\s*Original Message\s*-{2,}|"
    r"\nFrom:\s.{0,120}\nSent:\s|"
    r"\nSent from my i(?:Phone|Pad)\b",
    re.IGNORECASE,
)


def strip_quoted(text: str) -> str:
    """Truncate at the first sign of quoted/forwarded history, keeping only
    what this specific message actually added."""
    m = _QUOTE_MARKERS.search(text)
    return text[: m.start()] if m else text

# Phrases where the SENDER is proposing/offering their own availability —
# distinct from an incoming email that already states a fixed, confirmed time.
PROPOSAL_RE = re.compile(
    r"i(?:'|')?m\s+available|i\s+am\s+available|i(?:'|')?m\s+free|i\s+am\s+free|"
    r"i\s+can\s+do|i\s+could\s+do|works?\s+for\s+me|does\s+.{0,25}\s+work\s+for\s+you|"
    r"how\s+about|let(?:'|')s\s+do|i(?:'|')?m\s+open|"
    r"any\s+of\s+(?:these|the following)\s+(?:times?|work)|"
    r"either\s+.{0,40}\s+or\s+|"
    r"(?:^|\W)(?:free|available)\s+(?:on\s+)?(?:mon|tue|wed|thu|fri|sat|sun)",
    re.IGNORECASE,
)
# The OTHER party accepting one of the proposed times.
CONFIRM_RE = re.compile(
    r"\bconfirm(?:ed|ing)?\b|sounds?\s+good|see\s+you\s+then|that\s+works|"
    r"works\s+(?:for\s+me|great|well)|"
    r"(?:let'?s|we'll)\s+(?:go\s+with|do)\s+|"
    r"looking\s+forward\s+to\s+(?:it|our|speaking)|"
    r"i(?:'|')?ll\s+see\s+you|calendar\s+invite\s+(?:sent|to\s+follow)",
    re.IGNORECASE,
)
# The OTHER party turning down every offered slot / calling it off.
DECLINE_RE = re.compile(
    r"(?:can(?:'|')?t|unable\s+to|won(?:'|')?t\s+be\s+able\s+to)\s+make\s+it|"
    r"(?:none|neither)\s+of\s+(?:these|those)\s+(?:times?\s+)?work|"
    r"doesn(?:'|')?t\s+work\s+for\s+(?:me|us)|"
    r"need\s+to\s+resc?hedule|"
    r"(?:won(?:'|')?t|will\s+not)\s+be\s+moving\s+forward|"
    r"decided\s+to\s+(?:go|move)\s+(?:with|forward)\s+(?:another|a\s+different)",
    re.IGNORECASE,
)
# The OTHER party rejecting the offered slots but proposing a different one —
# a counter, not a flat decline; still "pending" but with new candidate slots.
COUNTER_RE = re.compile(
    r"(?:could\s+we|can\s+we|would\s+it\s+be\s+possible\s+to)\s+(?:do|move|push|reschedule)|"
    r"instead\s+(?:of|,)|"
    r"(?:any\s+chance|what\s+about)\s+.{0,20}\s+instead|"
    r"i(?:'|')?m\s+not\s+(?:available|free)\s+.{0,20}but",
    re.IGNORECASE,
)


def _next_weekday(now: datetime, target: int, explicit_next: bool) -> datetime:
    days_ahead = target - now.weekday()
    if days_ahead < 0 or (days_ahead == 0 and explicit_next):
        days_ahead += 7
    return now + timedelta(days=days_ahead)


def _apply_time(base: datetime, hour: int, minute: int, now: datetime) -> datetime:
    dt = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # A same-day resolution (bare weekday match with no "next", or "today")
    # whose time has already passed almost certainly means next week, not
    # "earlier today" — nobody proposes a meeting in the past.
    if dt.date() == now.date() and dt < now:
        dt += timedelta(days=7)
    return dt


def _parse_time_token(m: re.Match) -> tuple[int, int]:
    hour = int(m.group("h")) % 12
    minute = int(m.group("m") or 0)
    if "p" in m.group("ap").lower():
        hour += 12
    return hour, minute


def extract_slots(text: str, now: datetime | None = None) -> list[str]:
    """Return every distinct (date, time) pair mentioned, as Eastern ISO
    strings. Handles multiple options in one message ("Mon 2pm or Tue 10am"),
    weekday names ("next Friday"), relative days, and vague times.
    """
    now = (now or datetime.now(EASTERN)).astimezone(EASTERN)

    # Segment on connectors so "Monday at 10am or 2pm" pairs 2pm with Monday
    # too, while "Monday at 10am or Tuesday at 2pm" doesn't cross-pollinate.
    segments = re.split(r",|\bor\b|\band\b|;", text, flags=re.IGNORECASE)

    slots: list[datetime] = []
    last_date: datetime | None = None
    for seg in segments:
        seg_date = None
        dm = _DATE_RE.search(seg)
        if dm:
            month = MONTHS[dm.group("mon").lower()[:3]]
            day = int(dm.group("day"))
            year = now.year
            try:
                cand = datetime(year, month, day, tzinfo=EASTERN)
                if cand.date() < now.date():
                    cand = cand.replace(year=year + 1)
                seg_date = cand
            except ValueError:
                seg_date = None
        if seg_date is None:
            wm = _WEEKDAY_RE.search(seg)
            if wm:
                target = WEEKDAYS[wm.group("day").lower()]
                seg_date = _next_weekday(now, target, bool(wm.group("next")))
        if seg_date is None:
            rm = _RELATIVE_RE.search(seg)
            if rm:
                seg_date = now + timedelta(days=1 if rm.group(1).lower() == "tomorrow" else 0)

        if seg_date is not None:
            last_date = seg_date
        elif last_date is not None:
            seg_date = last_date
        else:
            continue  # no date context at all yet — nothing to anchor a time to

        found_time = False
        for tm in _TIME_RE.finditer(seg):
            hour, minute = _parse_time_token(tm)
            slots.append(_apply_time(seg_date, hour, minute, now))
            found_time = True
        if not found_time:
            vm = _VAGUE_TIME.search(seg)
            if vm:
                hour, minute = _VAGUE_DEFAULTS[vm.group(1).lower()]
                slots.append(_apply_time(seg_date, hour, minute, now))
                found_time = True
        if not found_time:
            # A bare date with no time anywhere nearby — still worth keeping
            # (e.g. "available Friday", or a weekday match with no time at
            # all) so it doesn't silently vanish from the proposed slots.
            slots.append(_apply_time(seg_date, 9, 0, now))

    # De-dupe while preserving order (same slot can match twice across segments).
    seen: set[str] = set()
    out: list[str] = []
    for s in slots:
        iso = s.isoformat()
        if iso not in seen:
            seen.add(iso)
            out.append(iso)
    return out


def is_availability_proposal(text: str) -> bool:
    return bool(PROPOSAL_RE.search(text)) and bool(
        _TIME_RE.search(text) or _VAGUE_TIME.search(text) or _WEEKDAY_RE.search(text) or _DATE_RE.search(text)
    )


def classify_reply(text: str) -> str:
    """Classify an incoming reply relative to a prior proposal: one of
    'confirmed', 'declined', 'countered', or 'unclear' (no clear signal —
    treat as still pending, don't act on it).
    """
    if DECLINE_RE.search(text):
        return "declined"
    if COUNTER_RE.search(text) and (_TIME_RE.search(text) or _WEEKDAY_RE.search(text) or _DATE_RE.search(text)):
        return "countered"
    if CONFIRM_RE.search(text):
        return "confirmed"
    return "unclear"
