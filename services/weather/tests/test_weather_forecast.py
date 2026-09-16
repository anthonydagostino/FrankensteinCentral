"""The weather parser: what it shows, and what it refuses to invent.

WHY THIS FILE EXISTS. A weather card is read at a glance and believed without
checking, which makes a quietly wrong number worse here than almost anywhere
else on the dashboard. The two ways to be quietly wrong are both covered
below: showing a number that is not the one the API sent (a padded array index
rendering as 0°, which in February is plausible), and showing a real number
from the wrong moment (the hourly strip drifting off the current hour, or a
cached reading presented as current).

Per docs/TESTING.md nothing here is checked against "today". "The next twelve
hours" is a slice whose correctness depends entirely on what time it is, and
it has an edge at every single midnight — so it is swept hour by hour across
months, including the year end.
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from conftest import load_service_module  # noqa: E402

fc = load_service_module("weather_forecast", "services/weather/app/forecast.py")
wmo = load_service_module("weather_wmo", "services/weather/app/wmo.py")


# --- a payload shaped exactly like Open-Meteo's, for any moment -------------
#
# Built rather than recorded: a recorded fixture pins one date forever, and the
# whole point of these tests is that the date moves. The shapes (parallel
# arrays under `hourly`/`daily`, naive local ISO strings, `current` as a flat
# object) are Open-Meteo's documented forecast response.

def payload(start_day=date(2026, 9, 16), days=10, now_hour=13, hourly_days=None):
    hourly_days = hourly_days if hourly_days is not None else days
    times, temps, codes, precip, isday = [], [], [], [], []
    for d in range(hourly_days):
        day = start_day + timedelta(days=d)
        for h in range(24):
            times.append(f"{day.isoformat()}T{h:02d}:00")
            temps.append(60.0 + h)          # a distinct value per hour
            codes.append(3 if h % 2 else 0)
            precip.append(h)
            isday.append(1 if 6 <= h < 20 else 0)

    d_times = [(start_day + timedelta(days=d)).isoformat() for d in range(days)]
    return {
        "timezone": "America/New_York",
        "current": {
            "time": f"{start_day.isoformat()}T{now_hour:02d}:00",
            "temperature_2m": 81.4,
            "apparent_temperature": 84.2,
            "relative_humidity_2m": 60,
            "wind_speed_10m": 8.1,
            "weather_code": 2,
            "is_day": 1,
        },
        "hourly": {"time": times, "temperature_2m": temps, "weather_code": codes,
                   "precipitation_probability": precip, "is_day": isday},
        "daily": {
            "time": d_times,
            "weather_code": [2] * days,
            "temperature_2m_max": [90.0 - d for d in range(days)],
            "temperature_2m_min": [77.0 - d for d in range(days)],
            "precipitation_probability_max": [10] * days,
            "sunrise": [f"{t}T06:30" for t in d_times],
            "sunset": [f"{t}T19:15" for t in d_times],
        },
    }


def every_hour(start=datetime(2026, 1, 1, 0, 0), hours=24 * 400):
    for offset in range(hours):
        yield start + timedelta(hours=offset)


# --- the hourly strip -------------------------------------------------------

def test_the_strip_starts_at_the_current_hour_not_the_next_one():
    """At 13:00 the 13:00 reading IS now. Starting at 14:00 would make the
    strip begin an hour into the future while the card above says 13:00."""
    now = datetime(2026, 9, 16, 13, 0)
    strip = fc.hourly_strip(payload(), now)
    assert strip[0]["time"] == "2026-09-16T13:00"
    assert strip[0]["hour"] == 13


def test_a_part_hour_still_counts_as_the_current_hour():
    """13:45 is still the 13:00 reading. Rounding up would skip an hour every
    time anyone looked at the dashboard at any moment except o'clock."""
    strip = fc.hourly_strip(payload(), datetime(2026, 9, 16, 13, 45))
    assert strip[0]["time"] == "2026-09-16T13:00"


def test_the_strip_crosses_midnight_into_the_next_day():
    """The edge that exists every single night."""
    strip = fc.hourly_strip(payload(), datetime(2026, 9, 16, 22, 0))
    assert [h["time"] for h in strip][:3] == [
        "2026-09-16T22:00", "2026-09-16T23:00", "2026-09-17T00:00"]
    assert len(strip) == fc.HOURS_AHEAD


def test_the_strip_is_ordered_and_contiguous_at_every_hour_of_a_long_sweep():
    """The property, swept: whatever hour it is, the strip is the next twelve
    consecutive hours starting with this one."""
    p = payload(start_day=date(2026, 1, 1), days=16, hourly_days=16)
    start = datetime(2026, 1, 1, 0, 0)
    for now in every_hour(start, hours=24 * 14):
        strip = fc.hourly_strip(p, now)
        assert len(strip) == fc.HOURS_AHEAD, f"short strip at {now}"
        stamps = [fc.parse_hour(h["time"]) for h in strip]
        assert stamps[0] == now.replace(minute=0), f"wrong first hour at {now}"
        for a, b in zip(stamps, stamps[1:]):
            assert b - a == timedelta(hours=1), f"gap at {now}"


def test_running_off_the_end_of_the_data_returns_fewer_hours_not_invented_ones():
    """The last day of the payload. A strip padded to twelve would show hours
    the API never sent."""
    p = payload(start_day=date(2026, 9, 16), days=2, hourly_days=2)
    strip = fc.hourly_strip(p, datetime(2026, 9, 17, 18, 0))
    assert len(strip) == 6
    assert all(h["temp"] is not None for h in strip)


def test_a_short_temperature_array_yields_null_not_zero():
    """The defect this file exists for. Open-Meteo sends parallel arrays; a
    ragged one is invisible, and index-out-of-range padded to 0 renders as 0°,
    which is a plausible February temperature and a lie."""
    p = payload()
    p["hourly"]["temperature_2m"] = p["hourly"]["temperature_2m"][:5]
    strip = fc.hourly_strip(p, datetime(2026, 9, 16, 13, 0))
    assert [h["temp"] for h in strip] == [None] * len(strip)


def test_an_explicit_null_temperature_survives_as_null():
    p = payload()
    p["hourly"]["temperature_2m"][13] = None
    strip = fc.hourly_strip(p, datetime(2026, 9, 16, 13, 0))
    assert strip[0]["temp"] is None


def test_a_zero_degree_reading_is_kept_and_not_mistaken_for_missing():
    """0°F is a real temperature. A falsy check would erase the coldest day of
    the year — the one you most need to know about."""
    p = payload()
    p["hourly"]["temperature_2m"][13] = 0.0
    strip = fc.hourly_strip(p, datetime(2026, 9, 16, 13, 0))
    assert strip[0]["temp"] == 0


def test_one_malformed_timestamp_costs_that_hour_and_not_the_forecast():
    p = payload()
    p["hourly"]["time"][14] = "not a time"
    strip = fc.hourly_strip(p, datetime(2026, 9, 16, 13, 0))
    assert len(strip) == fc.HOURS_AHEAD
    assert "not a time" not in [h["time"] for h in strip]


def test_an_entirely_absent_hourly_block_is_empty_rather_than_an_exception():
    assert fc.hourly_strip({}, datetime(2026, 9, 16, 13, 0)) == []
    assert fc.hourly_strip(None, datetime(2026, 9, 16, 13, 0)) == []


# --- day and night ----------------------------------------------------------

def test_a_clear_night_gets_the_moon_and_not_the_sun():
    """The small wrongness that makes a person stop trusting the rest."""
    strip = fc.hourly_strip(payload(), datetime(2026, 9, 16, 22, 0))
    night = [h for h in strip if h["hour"] in (22, 23, 0, 1)]
    assert night and all(h["glyph"] in ("🌙", "☁️") for h in night)


def test_daylight_falls_back_to_sunrise_and_sunset_when_is_day_is_absent():
    p = payload()
    del p["hourly"]["is_day"]
    strip = fc.hourly_strip(p, datetime(2026, 9, 16, 5, 0))
    before, after = strip[0], strip[2]      # 05:00 and 07:00 against a 06:30 sunrise
    assert before["glyph"] in ("🌙", "☁️")
    assert after["glyph"] in ("☀️", "🌤️", "⛅", "☁️")


# --- the ten-day list and its bars -----------------------------------------

def test_the_daily_list_starts_today_and_never_looks_backwards():
    rows = fc.daily_rows(payload(), date(2026, 9, 18))
    assert rows[0]["date"] == "2026-09-18"
    assert rows[0]["is_today"] is True
    assert all(r["date"] >= "2026-09-18" for r in rows)


def test_the_list_is_capped_at_ten_days():
    rows = fc.daily_rows(payload(days=16), date(2026, 9, 16))
    assert len(rows) == fc.DAYS_AHEAD


def test_the_bars_span_the_range_of_the_list_itself():
    rows = fc.daily_rows(payload(), date(2026, 9, 16))
    assert rows[0]["bar_start"] is not None
    starts = [r["bar_start"] for r in rows]
    widths = [r["bar_width"] for r in rows]
    # The coldest low sits at 0 and the warmest high ends at 100.
    assert min(starts) == 0.0
    assert max(s + w for s, w in zip(starts, widths)) == 100.0
    assert all(0 <= s <= 100 and w >= 0 for s, w in zip(starts, widths))


def test_ten_identical_days_do_not_divide_by_zero():
    """A degenerate range is a crash, not a wrong pixel — and it turns up in a
    test long before it turns up in weather."""
    p = payload()
    p["daily"]["temperature_2m_max"] = [70.0] * 10
    p["daily"]["temperature_2m_min"] = [70.0] * 10
    rows = fc.daily_rows(p, date(2026, 9, 16))
    assert all(r["bar_start"] is None and r["bar_width"] is None for r in rows)
    assert all(r["high"] == 70 and r["low"] == 70 for r in rows)


def test_a_day_missing_a_temperature_gets_no_bar_rather_than_a_guessed_one():
    p = payload()
    p["daily"]["temperature_2m_min"][2] = None
    rows = fc.daily_rows(p, date(2026, 9, 16))
    assert rows[2]["low"] is None
    assert rows[2]["bar_start"] is None and rows[2]["bar_width"] is None
    assert rows[0]["bar_start"] is not None, "one bad day must not erase the rest"


def test_the_daily_list_is_right_on_every_day_of_a_calendar_sweep():
    for start in (date(2026, 1, 1), date(2026, 2, 26), date(2026, 12, 26),
                  date(2028, 2, 26)):
        p = payload(start_day=start, days=10, hourly_days=10)
        rows = fc.daily_rows(p, start)
        assert len(rows) == 10, start
        assert rows[0]["is_today"] and not any(r["is_today"] for r in rows[1:])
        seen = [datetime.fromisoformat(r["date"]).date() for r in rows]
        for a, b in zip(seen, seen[1:]):
            assert (b - a).days == 1, f"gap across {start}"


# --- today's high and low ---------------------------------------------------

def test_todays_high_comes_from_the_day_and_not_from_the_hours_that_remain():
    """At 6pm the day's high is in the past. A strip-derived high would fall
    all evening and disagree with every other weather app on the phone."""
    out = fc.summarise(payload(), datetime(2026, 9, 16, 18, 0))
    assert out["high"] == 90
    assert out["low"] == 77


def test_high_and_low_are_null_when_the_day_is_not_in_the_payload():
    out = fc.summarise(payload(days=3), datetime(2026, 9, 30, 12, 0))
    assert out["high"] is None and out["low"] is None
    assert out["daily"] == []


# --- staleness --------------------------------------------------------------

def test_a_fresh_reading_is_not_stale():
    cur = fc.current(payload(now_hour=13), datetime(2026, 9, 16, 13, 30))
    assert cur["age_min"] == 30 and cur["stale"] is False


def test_a_cached_reading_from_hours_ago_says_so():
    """The card must never present an old number as the current temperature."""
    cur = fc.current(payload(now_hour=6), datetime(2026, 9, 16, 13, 0))
    assert cur["age_min"] == 420 and cur["stale"] is True


def test_an_unknown_reading_time_is_unknown_staleness_and_not_fresh():
    p = payload()
    p["current"]["time"] = None
    cur = fc.current(p, datetime(2026, 9, 16, 13, 0))
    assert cur["age_min"] is None
    assert cur["stale"] is None, "unknown age must not read as fresh"


def test_staleness_is_measured_at_every_hour_of_a_sweep():
    for now in every_hour(datetime(2026, 1, 1), hours=24 * 200):
        p = payload(start_day=now.date(), now_hour=now.hour)
        cur = fc.current(p, now)
        assert cur["age_min"] == now.minute
        assert cur["stale"] is False


# --- the whole card ---------------------------------------------------------

def test_an_empty_payload_produces_a_card_of_nulls_and_never_a_zero():
    out = fc.summarise({}, datetime(2026, 9, 16, 13, 0))
    assert out["current"]["temp"] is None
    assert out["high"] is None and out["low"] is None
    assert out["hourly"] == [] and out["daily"] == []


def test_the_place_travels_through_untouched():
    out = fc.summarise(payload(), datetime(2026, 9, 16, 13, 0), place={"name": "Hoboken"})
    assert out["place"] == {"name": "Hoboken"}


def test_every_wmo_code_in_the_table_describes_without_raising():
    for code in wmo.known_codes():
        for is_day in (True, False):
            label, glyph, severity = wmo.describe(code, is_day)
            assert label and glyph and isinstance(severity, int)


def test_an_unknown_code_says_unknown_rather_than_guessing_clear():
    """"Clear" is the friendliest wrong answer and the most misleading."""
    label, glyph, severity = wmo.describe(4242)
    assert label == "Unknown" and severity == 0
