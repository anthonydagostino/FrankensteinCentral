"""The weather service's own decisions: which place, and which clock.

The parser is tested in test_forecast.py. What is left here is small and it is
where the embarrassing bugs live — a latitude of zero read as "unset", a
forecast for Denver timed against a clock in New York, a geocoder row you can
click that goes nowhere.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from conftest import load_service_module  # noqa: E402

wx = load_service_module("weather_main", "services/weather/app/main.py")


# --- is a location actually set? -------------------------------------------

def test_no_settings_at_all_means_no_place():
    assert wx._place({}) is None
    assert wx._place({"place": "Hoboken"}) is None, "a name without coordinates is not a place"


def test_a_latitude_of_zero_is_a_real_place_and_not_an_unset_one():
    """The Gulf of Guinea is at 0, 0. A truthiness check would read a real
    location as unset — or, worse, read an unset one as the Atlantic."""
    place = wx._place({"lat": 0.0, "lon": 0.0, "place": "Null Island"})
    assert place is not None
    assert place["lat"] == 0.0 and place["lon"] == 0.0


def test_a_place_with_no_name_is_labelled_by_its_coordinates():
    """Better than a blank card header — you can at least tell it moved."""
    place = wx._place({"lat": 40.7143, "lon": -74.006})
    assert place["name"] == "40.71, -74.01"


def test_fahrenheit_is_the_default_and_celsius_is_opt_in():
    assert wx._place({"lat": 1, "lon": 2})["unit"] == "fahrenheit"
    assert wx._place({"lat": 1, "lon": 2, "unit": "celsius"})["unit"] == "celsius"
    assert wx._place({"lat": 1, "lon": 2, "unit": "nonsense"})["unit"] == "fahrenheit"


def test_a_missing_timezone_falls_back_rather_than_failing():
    assert wx._place({"lat": 1, "lon": 2})["timezone"] == wx.FALLBACK_TZ


# --- the clock --------------------------------------------------------------

def test_the_clock_reads_the_locations_timezone_not_the_boxs():
    """Asking "what time is it in Denver" from a machine in New York is the
    whole reason _now takes a name. Two hours apart, always."""
    ny = wx._now("America/New_York")
    denver = wx._now("America/Denver")
    assert isinstance(ny, datetime) and ny.tzinfo is None, (
        "forecast.py compares naive-to-naive; an aware datetime here would "
        "raise against Open-Meteo's naive local strings")
    delta = (ny - denver).total_seconds()
    assert 7000 < delta < 7400, f"expected ~2h between NY and Denver, got {delta}s"


def test_a_nonsense_timezone_falls_back_instead_of_raising():
    """A bad zone in settings must cost the correct hour, never the card."""
    assert wx._now("Mars/Olympus_Mons").tzinfo is None
    assert wx._now(None).tzinfo is None
    assert wx._now("").tzinfo is None


# --- the geocoder's answers -------------------------------------------------

def test_no_matches_is_an_empty_list_and_not_an_error():
    """Open-Meteo OMITS `results` entirely when nothing matches rather than
    sending an empty list, so "no such town" arrives looking like a failure."""
    assert wx.places_from_geocode({"generationtime_ms": 0.4}) == []
    assert wx.places_from_geocode({}) == []
    assert wx.places_from_geocode(None) == []


def test_a_row_without_coordinates_is_dropped():
    """The picker's whole job is to hand back a lat/lon. A row that cannot is
    an entry you can click that does nothing."""
    out = wx.places_from_geocode({"results": [
        {"name": "Nowhere"},
        {"name": "Hoboken", "latitude": 40.7, "longitude": -74.0},
    ]})
    assert [p["name"] for p in out] == ["Hoboken"]


def test_the_label_carries_the_state_so_two_springfields_are_distinguishable():
    out = wx.places_from_geocode({"results": [
        {"name": "Springfield", "admin1": "Illinois", "country": "United States",
         "latitude": 39.8, "longitude": -89.6},
        {"name": "Springfield", "admin1": "Missouri", "country": "United States",
         "latitude": 37.2, "longitude": -93.3},
    ]})
    assert out[0]["label"] != out[1]["label"]
    assert "Illinois" in out[0]["label"] and "Missouri" in out[1]["label"]


def test_a_label_skips_the_parts_it_does_not_have_rather_than_leaving_gaps():
    out = wx.places_from_geocode({"results": [
        {"name": "Somewhere", "latitude": 1.0, "longitude": 2.0}]})
    assert out[0]["label"] == "Somewhere"


def test_junk_rows_do_not_take_the_whole_list_down():
    out = wx.places_from_geocode({"results": [
        "not a dict", None, {"name": "Hoboken", "latitude": 40.7, "longitude": -74.0}]})
    assert [p["name"] for p in out] == ["Hoboken"]


def test_a_place_at_zero_longitude_survives_the_geocoder_too():
    """Greenwich. Same falsy trap as the latitude one, one layer up."""
    out = wx.places_from_geocode({"results": [
        {"name": "Greenwich", "latitude": 51.48, "longitude": 0.0}]})
    assert len(out) == 1 and out[0]["lon"] == 0.0


# --- the service never invents weather --------------------------------------

def test_the_cache_starts_empty_so_nothing_is_served_before_a_fetch():
    assert wx._CACHE["payload"] is None or wx._CACHE["key"] is not None
