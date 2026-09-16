"""Actual weather for a place you choose.

Open-Meteo, which needs no API key — the same reason `stocks` uses Stooq. A
feature that stops working when a free tier lapses is a feature that breaks
silently six months from now, on a dashboard nobody is auditing.

WHERE THE LOCATION LIVES. In `core` settings under `weather`, the same place
`market.holdings` lives, so it is edited in one place and survives a rebuild of
this container. This service stores nothing: no database, no volume, no state.
That is deliberate — on 2026-09-16 a new service failed to start on the box and
took the dashboard down with it, and the cheapest way not to repeat that is to
have less that can fail.

THREE STATES, NOT TWO. `not_configured` (no location chosen yet),
`unreachable` (we asked and could not get an answer) and `ok` are different
things, and the card says which. An unreachable forecast must never render as
a temperature, because the one number a person takes from this card without
reading it is the big one.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import FastAPI

from . import forecast

app = FastAPI(title="Weather Service")

CORE_URL = os.environ.get("CORE_URL", "http://core:8000")
FORECAST_API = os.environ.get(
    "WEATHER_API_URL", "https://api.open-meteo.com/v1/forecast")
GEOCODE_API = os.environ.get(
    "WEATHER_GEOCODE_URL", "https://geocoding-api.open-meteo.com/v1/search")
FALLBACK_TZ = os.environ.get("LOCAL_TZ", "America/New_York")

# Weather is not a quote. Ten minutes is finer than the data's own hourly
# resolution and keeps a dashboard that refreshes itself from hammering a free
# service that asked for nothing in return.
CACHE_TTL_SEC = 600
_CACHE = {"key": None, "at": None, "payload": None}

HTTP_TIMEOUT = 8.0


def _now(tz_name):
    """The single clock read, in the LOCATION's timezone rather than the box's.

    Open-Meteo returns naive local wall-clock when `timezone=` is set, so every
    comparison in forecast.py is naive-to-naive and both sides have to mean the
    same place. Asking "what time is it in Denver" from a machine in New York
    is the whole reason this takes a name.
    """
    try:
        zone = ZoneInfo(tz_name or FALLBACK_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(FALLBACK_TZ)
    return datetime.now(zone).replace(tzinfo=None)


async def _settings():
    """The saved location, or {} if core cannot be reached.

    Never raises: a settings read that throws would turn a core hiccup into a
    crashed weather request, and the card already knows how to say it does not
    have a location.
    """
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get(f"{CORE_URL}/settings")
            r.raise_for_status()
            data = r.json()
    except Exception:
        return {}
    return (data or {}).get("weather") or {}


def _place(settings):
    """The chosen location, or None when nothing has been chosen.

    A latitude of 0.0 is a real place (the Gulf of Guinea), so "is it set" is a
    None check and never a truthiness one — the bug would put every unset
    dashboard in the Atlantic rather than saying nothing is set.
    """
    lat, lon = settings.get("lat"), settings.get("lon")
    if lat is None or lon is None:
        return None
    return {
        "name": settings.get("place") or f"{lat:.2f}, {lon:.2f}",
        "lat": float(lat),
        "lon": float(lon),
        "timezone": settings.get("timezone") or FALLBACK_TZ,
        "unit": "celsius" if settings.get("unit") == "celsius" else "fahrenheit",
    }


async def _fetch(place):
    """The forecast payload, or None if the API could not be reached.

    None and not {}: an empty dict would flow through the parser and produce a
    card full of nulls that claims state "ok", which reads as "we looked and
    there is no weather".
    """
    key = (place["lat"], place["lon"], place["unit"])
    now = datetime.utcnow()
    if (_CACHE["key"] == key and _CACHE["at"]
            and (now - _CACHE["at"]).total_seconds() < CACHE_TTL_SEC):
        return _CACHE["payload"]

    params = {
        "latitude": place["lat"],
        "longitude": place["lon"],
        "timezone": place["timezone"],
        "temperature_unit": place["unit"],
        "wind_speed_unit": "mph" if place["unit"] == "fahrenheit" else "kmh",
        "precipitation_unit": "inch" if place["unit"] == "fahrenheit" else "mm",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                   "is_day,weather_code,wind_speed_10m",
        "hourly": "temperature_2m,weather_code,precipitation_probability,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max,sunrise,sunset",
        "forecast_days": forecast.DAYS_AHEAD,
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get(FORECAST_API, params=params)
            r.raise_for_status()
            payload = r.json()
    except Exception:
        # The previous answer is better than nothing AND better than a lie:
        # forecast.current() timestamps every reading, so a served-from-cache
        # temperature carries its own age and the card marks it stale.
        return _CACHE["payload"] if _CACHE["key"] == key else None

    _CACHE.update(key=key, at=now, payload=payload)
    return payload


@app.get("/health")
async def health():
    settings = await _settings()
    place = _place(settings)
    return {
        "service": "weather",
        # Distinguishes "nobody has chosen a place" from "the upstream is
        # down", which are the two ways this can show nothing.
        "configured": place is not None,
        "place": place["name"] if place else None,
        "cached": _CACHE["payload"] is not None,
        "source": "open-meteo",
    }


@app.get("/current")
async def current():
    """Now, the next twelve hours, and the next ten days."""
    place = _place(await _settings())
    if not place:
        return {"state": "not_configured", "place": None, "current": None,
                "high": None, "low": None, "hourly": [], "daily": [],
                "unit": None}

    payload = await _fetch(place)
    if payload is None:
        return {"state": "unreachable", "place": place, "current": None,
                "high": None, "low": None, "hourly": [], "daily": [],
                "unit": place["unit"]}

    out = forecast.summarise(payload, _now(place["timezone"]), place=place)
    # The unit is carried rather than assumed. A °C reading rendered under a
    # °F label is a number that is wrong by forty degrees and looks fine.
    out["unit"] = place["unit"]
    out["degree"] = "°F" if place["unit"] == "fahrenheit" else "°C"
    return out


@app.get("/search")
async def search(q: str = "", count: int = 6):
    """Find a place by name, for the location picker.

    Open-Meteo's geocoder OMITS the `results` key entirely when nothing
    matches, rather than sending an empty list — so "no matches" and "the
    request failed" have to be told apart here, not by the caller looking for
    a falsy value.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return {"state": "ok", "query": q, "results": []}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get(GEOCODE_API, params={
                "name": q, "count": max(1, min(count, 10)),
                "language": "en", "format": "json"})
            r.raise_for_status()
            data = r.json() or {}
    except Exception:
        return {"state": "unreachable", "query": q, "results": []}

    return {"state": "ok", "query": q, "results": places_from_geocode(data)}


def places_from_geocode(data):
    """The geocoder's rows, reshaped into what the picker needs.

    Pure, so the shape is tested rather than trusted. Rows without coordinates
    are dropped: the picker's whole job is to hand back a lat/lon, and an entry
    that cannot is a row you can click that does nothing.
    """
    out = []
    for row in ((data or {}).get("results") or []):
        if not isinstance(row, dict):
            continue
        lat, lon = row.get("latitude"), row.get("longitude")
        if lat is None or lon is None:
            continue
        name = row.get("name") or ""
        admin1 = row.get("admin1") or ""
        country = row.get("country") or ""
        out.append({
            "name": name,
            "admin1": admin1,
            "country": country,
            "lat": lat,
            "lon": lon,
            "timezone": row.get("timezone") or FALLBACK_TZ,
            # Two Springfields in one list are indistinguishable without the
            # state, and picking the wrong one is a forecast for a place you
            # have never been that looks entirely reasonable.
            "label": ", ".join(part for part in (name, admin1, country) if part),
        })
    return out


@app.get("/")
async def root():
    return {"app": "Weather", "source": "open-meteo",
            "endpoints": ["/current", "/search", "/health"]}
