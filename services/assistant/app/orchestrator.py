import re
from datetime import datetime

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DATE_RE = re.compile(
    r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?P<day>\d{1,2})",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)", re.IGNORECASE)


def extract_datetime(text: str, default_year: int | None = None) -> str | None:
    """Best-effort parse of a date/time from free text into an ISO string.

    Returns None when no date is found. Used to turn 'Interview scheduled:
    Fri Jul 25, 2:00 PM' into a calendar timestamp.
    """
    year = default_year or datetime.utcnow().year
    dm = _DATE_RE.search(text)
    if not dm:
        return None
    month = MONTHS[dm.group("mon").lower()[:3]]
    day = int(dm.group("day"))

    hour, minute = 9, 0
    tm = _TIME_RE.search(text)
    if tm:
        hour = int(tm.group("h")) % 12
        minute = int(tm.group("m") or 0)
        if tm.group("ap").lower() == "pm":
            hour += 12
    try:
        return datetime(year, month, day, hour, minute).isoformat()
    except ValueError:
        return None
