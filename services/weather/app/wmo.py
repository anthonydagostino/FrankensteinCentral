"""WMO 4677 weather codes, which is what Open-Meteo speaks.

The API returns an integer. Everything a person reads — "Mostly Cloudy", the
glyph, whether it is the kind of weather you take a coat for — is derived here,
in one table, rather than in three places that drift apart.

WHY A TABLE AND NOT RANGES. It is tempting to write `61 <= code <= 65: rain`.
The codes are not contiguous in meaning: 56/57 are freezing drizzle sitting
between drizzle and rain, 66/67 are freezing rain sitting between rain and
snow, and 77 (snow grains) sits outside the 71-75 snow run. A range test reads
cleanly and mislabels freezing rain as rain, which is the difference between
"wet" and "the roads are ice".
"""

# code: (label, day glyph, night glyph, severity)
#
# `severity` is 0 for benign and rises with how much the weather should change
# your plans. The dashboard uses it to decide whether the card earns a warning
# colour, so it is a judgement about the DAY, not about meteorology: fog scores
# above drizzle because it changes a drive and drizzle does not.
_CODES = {
    0:  ("Clear", "☀️", "🌙", 0),
    1:  ("Mainly clear", "🌤️", "🌙", 0),
    2:  ("Partly cloudy", "⛅", "☁️", 0),
    3:  ("Overcast", "☁️", "☁️", 0),
    45: ("Fog", "🌫️", "🌫️", 2),
    48: ("Freezing fog", "🌫️", "🌫️", 3),
    51: ("Light drizzle", "🌦️", "🌧️", 1),
    53: ("Drizzle", "🌦️", "🌧️", 1),
    55: ("Heavy drizzle", "🌧️", "🌧️", 2),
    56: ("Freezing drizzle", "🌧️", "🌧️", 3),
    57: ("Heavy freezing drizzle", "🌧️", "🌧️", 3),
    61: ("Light rain", "🌦️", "🌧️", 1),
    63: ("Rain", "🌧️", "🌧️", 2),
    65: ("Heavy rain", "🌧️", "🌧️", 3),
    66: ("Freezing rain", "🌨️", "🌨️", 4),
    67: ("Heavy freezing rain", "🌨️", "🌨️", 4),
    71: ("Light snow", "🌨️", "🌨️", 2),
    73: ("Snow", "❄️", "❄️", 3),
    75: ("Heavy snow", "❄️", "❄️", 4),
    77: ("Snow grains", "🌨️", "🌨️", 2),
    80: ("Light showers", "🌦️", "🌧️", 1),
    81: ("Showers", "🌧️", "🌧️", 2),
    82: ("Violent showers", "⛈️", "⛈️", 4),
    85: ("Light snow showers", "🌨️", "🌨️", 2),
    86: ("Heavy snow showers", "❄️", "❄️", 4),
    95: ("Thunderstorm", "⛈️", "⛈️", 4),
    96: ("Thunderstorm with hail", "⛈️", "⛈️", 5),
    99: ("Thunderstorm with heavy hail", "⛈️", "⛈️", 5),
}

# What an unrecognised code becomes. Open-Meteo can add codes, and a KeyError
# in a weather card is a blank dashboard over a number nobody needed. The label
# says the truth — we do not know — rather than guessing "Clear", which is both
# the friendliest wrong answer and the most misleading.
UNKNOWN = ("Unknown", "❓", "❓", 0)


def describe(code, is_day=True):
    """(label, glyph, severity) for a WMO code.

    `is_day` picks the glyph only. A clear night is not a sunny night, and the
    label is the same word either way — the sun in a moonlit forecast is the
    kind of small wrongness that makes a person stop trusting the rest.
    """
    label, day, night, severity = _CODES.get(_as_int(code), UNKNOWN)
    return label, (day if is_day else night), severity


def _as_int(code):
    """Codes arrive from JSON, which may hand back a float or a string. None
    stays None so it misses the table and reads as Unknown."""
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def known_codes():
    """For the tests, so the sweep covers the table rather than a sample."""
    return sorted(_CODES)
