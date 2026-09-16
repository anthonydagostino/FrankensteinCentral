"""Turning an Open-Meteo payload into the shape the dashboard renders.

Everything here is pure, so per docs/TESTING.md the hour- and day-picking is
swept across a calendar rather than checked against "today". That matters more
than it looks: "the next twelve hours" is a slice whose correctness depends
entirely on what time it is, and it has an edge at every midnight.

THE RULE THIS FILE KEEPS. A missing number stays missing. Open-Meteo returns
parallel arrays, and a short or ragged one is not an error you can see — index
5 of `temperature_2m` simply is not there. Padding it with 0 renders as 0°,
which in February is a plausible temperature and a lie. Every reader below
returns None instead, and the card is built to say "—".

TIMES ARE NAIVE AND LOCAL. With `timezone=` set, Open-Meteo returns local wall
clock with no offset ("2026-09-16T13:00"). So every comparison in here is
naive-to-naive, and the caller is responsible for handing in a `now` already
converted to the location's timezone. Mixing an aware `now` with these strings
raises rather than silently comparing wrong, which is the good failure.
"""
from datetime import datetime, timedelta

from . import wmo

# How much of the future each surface shows. The hourly strip is a glance and
# the daily list is a plan, which is why they are different lengths.
HOURS_AHEAD = 12
DAYS_AHEAD = 10

# A forecast older than this is reported as stale rather than as current. The
# hourly resolution is an hour, so anything within one is as fresh as the data
# can be; past three, the "now" temperature is a number from another part of
# the day.
STALE_AFTER_MIN = 180


def parse_hour(text):
    """"2026-09-16T13:00" -> datetime, or None if it is not a time.

    None rather than an exception: one malformed entry in a 168-hour array
    should cost that hour, not the whole forecast.
    """
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _at(seq, i):
    """Element i of a possibly-short, possibly-absent array, or None."""
    if not isinstance(seq, (list, tuple)) or i < 0 or i >= len(seq):
        return None
    return seq[i]


def _num(value):
    """A float, or None. Open-Meteo uses null for a value it does not have, and
    that null must survive all the way to the card as "—"."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value, digits=0):
    n = _num(value)
    if n is None:
        return None
    return round(n, digits) if digits else int(round(n))


def hourly_strip(payload, now, hours=HOURS_AHEAD):
    """The next `hours` hourly readings at or after `now`, oldest first.

    "At or after" and not "after": at 13:00 the 13:00 reading is the current
    hour, and dropping it would make the strip start an hour into the future
    while the card above it says it is 13:00.
    """
    block = (payload or {}).get("hourly") or {}
    times = block.get("time") or []
    out = []
    for i, raw in enumerate(times):
        when = parse_hour(raw)
        if when is None or when < now.replace(minute=0, second=0, microsecond=0):
            continue
        code = _at(block.get("weather_code"), i)
        label, glyph, _ = wmo.describe(code, is_day=_is_daylight(when, payload, i))
        out.append({
            "time": raw,
            "hour": when.hour,
            "temp": _round(_at(block.get("temperature_2m"), i)),
            "code": code,
            "label": label,
            "glyph": glyph,
            "precip_pct": _round(_at(block.get("precipitation_probability"), i)),
        })
        if len(out) >= hours:
            break
    return out


def _is_daylight(when, payload, index):
    """Day or night for an hourly glyph.

    Open-Meteo offers an `is_day` hourly field; when it was not requested or
    the array is short, fall back to the day's own sunrise/sunset, and only
    then to a plain hour-of-day rule. Each fallback is worse than the last and
    none of them can fail, because a wrong glyph must never cost the forecast.
    """
    flag = _at(((payload or {}).get("hourly") or {}).get("is_day"), index)
    if flag is not None:
        return bool(flag)

    daily = (payload or {}).get("daily") or {}
    for i, day in enumerate(daily.get("time") or []):
        if day == when.date().isoformat():
            rise = parse_hour(_at(daily.get("sunrise"), i))
            sets = parse_hour(_at(daily.get("sunset"), i))
            if rise and sets:
                return rise <= when < sets
            break
    return 6 <= when.hour < 20


def daily_rows(payload, today, days=DAYS_AHEAD):
    """One row per day from `today` forward, with the bar geometry.

    The bar is the iOS Weather idea: each day's high-low drawn against the
    range of the whole list, so a cold snap is visible as a bar that sits low
    rather than as ten numbers you have to compare yourself.
    """
    block = (payload or {}).get("daily") or {}
    rows = []
    for i, raw in enumerate(block.get("time") or []):
        try:
            day = datetime.fromisoformat(raw).date()
        except (TypeError, ValueError):
            continue
        if day < today:
            continue
        code = _at(block.get("weather_code"), i)
        label, glyph, severity = wmo.describe(code, is_day=True)
        rows.append({
            "date": raw,
            "weekday": day.strftime("%a"),
            "is_today": day == today,
            "high": _round(_at(block.get("temperature_2m_max"), i)),
            "low": _round(_at(block.get("temperature_2m_min"), i)),
            "code": code,
            "label": label,
            "glyph": glyph,
            "severity": severity,
            "precip_pct": _round(_at(block.get("precipitation_probability_max"), i)),
        })
        if len(rows) >= days:
            break
    return _with_bars(rows)


def _with_bars(rows):
    """Add `bar_start`/`bar_width` as percentages of the list's own range.

    Days missing a high or a low get no bar rather than a bar from an invented
    number — `null`, which the card renders as no bar at all, is the honest
    shape of "we were not told".
    """
    lows = [r["low"] for r in rows if r["low"] is not None]
    highs = [r["high"] for r in rows if r["high"] is not None]
    lo, hi = (min(lows), max(highs)) if lows and highs else (None, None)
    # Ten days that are all exactly the same temperature is a degenerate range,
    # and dividing by it is a crash rather than a wrong pixel. It happens in
    # tests long before it happens in weather, which is reason enough.
    span = (hi - lo) if (lo is not None and hi > lo) else None

    for r in rows:
        if span is None or r["low"] is None or r["high"] is None:
            r["bar_start"], r["bar_width"] = None, None
            continue
        r["bar_start"] = round((r["low"] - lo) / span * 100, 1)
        r["bar_width"] = round((r["high"] - r["low"]) / span * 100, 1)
    return rows


def current(payload, now):
    """Right now, plus how old "now" actually is.

    `as_of` is the API's own timestamp, not ours. A cached forecast served an
    hour later must not claim to be current, and the only way the card can say
    so is if this number is the reading's, not the request's.
    """
    block = (payload or {}).get("current") or {}
    when = parse_hour(block.get("time"))
    age = int((now - when).total_seconds() // 60) if when else None
    is_day = block.get("is_day")
    label, glyph, severity = wmo.describe(
        block.get("weather_code"),
        is_day=bool(is_day) if is_day is not None else _is_daylight(when or now, payload, -1),
    )
    return {
        "temp": _round(block.get("temperature_2m")),
        "feels_like": _round(block.get("apparent_temperature")),
        "humidity_pct": _round(block.get("relative_humidity_2m")),
        "wind_mph": _round(block.get("wind_speed_10m")),
        "code": block.get("weather_code"),
        "label": label,
        "glyph": glyph,
        "severity": severity,
        "as_of": block.get("time"),
        "age_min": age,
        # A negative age means the reading is timestamped in the future, which
        # happens when the location's timezone and ours disagree. Saying so is
        # better than showing "-540 minutes old" or hiding it.
        "stale": None if age is None else age > STALE_AFTER_MIN,
    }


def summarise(payload, now, place=None):
    """The whole card: now, the next twelve hours, and the next ten days."""
    today = now.date()
    rows = daily_rows(payload, today)
    todays = next((r for r in rows if r["is_today"]), None)
    return {
        "state": "ok",
        "place": place,
        "current": current(payload, now),
        # Today's high and low come from the DAILY block, not from the hours
        # that remain. At 6pm the day's high is in the past; a strip-derived
        # "high" would quietly fall all evening and disagree with every other
        # weather app on the phone next to it.
        "high": todays["high"] if todays else None,
        "low": todays["low"] if todays else None,
        "hourly": hourly_strip(payload, now),
        "daily": rows,
    }


def next_hours_available(payload, now):
    """How many of the requested hours the payload could actually supply.

    Separate from the strip so the card can say "6 hours" honestly rather than
    rendering a short strip that looks like a full one.
    """
    return len(hourly_strip(payload, now, hours=HOURS_AHEAD))
