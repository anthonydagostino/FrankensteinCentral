"""The Amex credit catalogue, and the period arithmetic around it.

Nothing here does I/O, so all of it is swept across a calendar in the tests
rather than checked against "today" — per docs/TESTING.md. Period boundaries
are exactly the kind of date logic that works all year and breaks on the 31st
of December.

WHY THE CATALOGUE IS DATA. These benefits are not facts about the world, they
are terms Amex can change and does: the Platinum's Saks credit was withdrawn
on 1 July 2026 after Saks Global filed for bankruptcy, and the Platinum was
refreshed to an $895 fee with credits that did not exist the year before. They
also differ between card versions and between cardmembers. So the catalogue is
a table you can edit, each row carries the date it was last checked, and the
service reports that date rather than implying the list is authoritative.

VERIFY AGAINST YOUR OWN CARD. Several of these require ENROLLMENT before a
purchase counts — an unenrolled credit pays nothing and looks identical to one
you simply did not use. `enroll` marks those.
"""
from datetime import date

# When a human last reconciled this table against Amex's own benefit pages.
# Surfaced by the API so a stale catalogue announces itself instead of being
# quietly believed.
CATALOGUE_CHECKED = "2026-09-16"

MONTHLY = "monthly"
QUARTERLY = "quarterly"
SEMIANNUAL = "semiannual"
ANNUAL = "annual"

CADENCES = (MONTHLY, QUARTERLY, SEMIANNUAL, ANNUAL)

# Periods per calendar year, used to turn a per-period amount into a yearly one.
PERIODS_PER_YEAR = {MONTHLY: 12, QUARTERLY: 4, SEMIANNUAL: 2, ANNUAL: 1}


def _credit(key, card, name, amount, cadence, where, enroll=False, note="",
            annual_max=None):
    """`annual_max` overrides the arithmetic for the credits whose year does
    not equal the per-period amount times the number of periods. There is one
    today — the Platinum's Uber Cash — and deriving it would quietly report
    $180 for a benefit Amex sells as $200."""
    return {"key": key, "card": card, "name": name, "amount": amount,
            "cadence": cadence, "where": where, "enroll": enroll, "note": note,
            "annual_max": round(
                amount * PERIODS_PER_YEAR[cadence] if annual_max is None
                else annual_max, 2)}


# --- The Platinum Card, $895 annual fee --------------------------------------
PLATINUM = [
    _credit("plat_uber", "platinum", "Uber Cash", 15.00, MONTHLY,
            "Uber rides and Uber Eats, US",
            note="December is $35, not $15 — the annual total is $200, not $180.",
            annual_max=200.00),
    _credit("plat_digital", "platinum", "Digital entertainment", 25.00, MONTHLY,
            "Disney+, Hulu, ESPN, Peacock, NYT, WSJ", enroll=True),
    _credit("plat_walmart", "platinum", "Walmart+ membership", 12.95, MONTHLY,
            "Walmart+ monthly membership (not the annual plan)"),
    _credit("plat_resy", "platinum", "Resy dining", 100.00, QUARTERLY,
            "Eligible US Resy restaurants", enroll=True),
    _credit("plat_lululemon", "platinum", "lululemon", 75.00, QUARTERLY,
            "lululemon US stores and online, excluding outlets", enroll=True),
    _credit("plat_hotel", "platinum", "Fine Hotels + Resorts / Hotel Collection",
            300.00, SEMIANNUAL, "Prepaid FHR or THC bookings via Amex Travel",
            note="The Hotel Collection requires a two-night minimum."),
    _credit("plat_airline", "platinum", "Airline fee credit", 200.00, ANNUAL,
            "Incidentals on ONE airline you nominate", enroll=True,
            note="You pick the airline once a year; it is not any airline."),
    _credit("plat_clear", "platinum", "CLEAR Plus", 209.00, ANNUAL,
            "CLEAR Plus membership", enroll=True),
    _credit("plat_equinox", "platinum", "Equinox", 300.00, ANNUAL,
            "Equinox club membership or Equinox+ , auto-renewing", enroll=True),
    _credit("plat_oura", "platinum", "Oura Ring", 200.00, ANNUAL,
            "ouraring.com", enroll=True),
    _credit("plat_uber_one", "platinum", "Uber One membership", 120.00, ANNUAL,
            "Auto-renewing Uber One membership", enroll=True),
]

# --- The Gold Card, $325 annual fee ------------------------------------------
GOLD = [
    _credit("gold_uber", "gold", "Uber Cash", 10.00, MONTHLY,
            "Uber rides and Uber Eats, US",
            note="Add the Gold Card in the Uber app; it does not apply itself."),
    _credit("gold_dining", "gold", "Dining credit", 10.00, MONTHLY,
            "Grubhub, Five Guys, Cheesecake Factory, Wonder, Goldbelly",
            enroll=True),
    _credit("gold_dunkin", "gold", "Dunkin'", 7.00, MONTHLY,
            "US Dunkin' locations", enroll=True,
            note="Needs a single purchase of $7 or more to draw the whole credit."),
    _credit("gold_resy", "gold", "Resy dining", 50.00, SEMIANNUAL,
            "Eligible US Resy restaurants", enroll=True),
]

CARDS = {
    "platinum": {"key": "platinum", "name": "The Platinum Card",
                 "annual_fee": 895.00, "credits": PLATINUM},
    "gold": {"key": "gold", "name": "The Gold Card",
             "annual_fee": 325.00, "credits": GOLD},
}

ALL_CREDITS = PLATINUM + GOLD
BY_KEY = {c["key"]: c for c in ALL_CREDITS}


# --- period arithmetic -------------------------------------------------------

def period_of(cadence, today):
    """The (start, end) dates of the period `today` falls in, both inclusive.

    Every Amex credit here resets on a CALENDAR boundary, not on your account
    anniversary — quarters begin 1 Jan, 1 Apr, 1 Jul, 1 Oct. That is why this
    takes a plain date and not a cardmember.
    """
    if cadence == MONTHLY:
        start = today.replace(day=1)
        end = _last_day_of_month(today.year, today.month)
    elif cadence == QUARTERLY:
        first_month = 3 * ((today.month - 1) // 3) + 1
        start = date(today.year, first_month, 1)
        end = _last_day_of_month(today.year, first_month + 2)
    elif cadence == SEMIANNUAL:
        first_month = 1 if today.month <= 6 else 7
        start = date(today.year, first_month, 1)
        end = _last_day_of_month(today.year, first_month + 5)
    elif cadence == ANNUAL:
        start = date(today.year, 1, 1)
        end = date(today.year, 12, 31)
    else:
        raise ValueError(f"unknown cadence: {cadence!r}")
    return start, end


def _last_day_of_month(year, month):
    """Without calendar.monthrange, so the leap-year rule is visible and the
    tests sweep it rather than trusting it."""
    if month == 12:
        return date(year, 12, 31)
    first_of_next = date(year, month + 1, 1)
    return date.fromordinal(first_of_next.toordinal() - 1)


def period_id(cadence, today):
    """A stable string naming this period, e.g. "2026-M09", "2026-Q3".

    Redemptions are keyed on it, so marking September's Dunkin' credit used
    cannot leak into October's, and re-marking the same period is idempotent.
    """
    if cadence == MONTHLY:
        return f"{today.year}-M{today.month:02d}"
    if cadence == QUARTERLY:
        return f"{today.year}-Q{(today.month - 1) // 3 + 1}"
    if cadence == SEMIANNUAL:
        return f"{today.year}-H{1 if today.month <= 6 else 2}"
    if cadence == ANNUAL:
        return f"{today.year}-Y"
    raise ValueError(f"unknown cadence: {cadence!r}")


def days_left(cadence, today):
    """Days remaining in the period, counting today as one of them.

    Inclusive because that is how a deadline is read by a person: on the last
    day of the month you have one day left, not zero. A zero here would mean
    the period is already over, which cannot happen for a date inside it.
    """
    _, end = period_of(cadence, today)
    return (end - today).days + 1


def december_uber_bonus(credit, today):
    """The Platinum's Uber Cash is $15 a month except December, which is $35.

    Amex documents it as "$200 a year", and 15 x 12 is 180. The extra $20 lands
    in December. Encoded rather than averaged because a tracker that shows $15
    in December would under-report the one month it matters.
    """
    if credit["key"] == "plat_uber" and today.month == 12:
        return 35.00
    return credit["amount"]


# --- what is actually at risk ------------------------------------------------
#
# The point of the tracker is not a list of benefits — Amex already has one of
# those. It is the answer to "what expires soonest, and how much of it".

def summarise(today, used_keys, cards=("platinum", "gold")):
    """Every credit's state for `today`, plus the totals worth acting on.

    `used_keys` is the set of "<credit key>:<period id>" strings already
    redeemed. Keying on the period is what makes marking one used a fact about
    THAT month rather than about the credit forever.
    """
    rows, at_risk, captured, available = [], 0.0, 0.0, 0.0
    for card_key in cards:
        card = CARDS.get(card_key)
        if not card:
            continue
        for credit in card["credits"]:
            pid = period_id(credit["cadence"], today)
            start, end = period_of(credit["cadence"], today)
            amount = december_uber_bonus(credit, today)
            is_used = f"{credit['key']}:{pid}" in used_keys
            left = days_left(credit["cadence"], today)
            rows.append({
                **credit,
                "amount": amount,
                "period_id": pid,
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "days_left": left,
                "used": is_used,
                # "Urgent" is a week, not a fixed number of days per cadence: a
                # monthly credit with 5 days left and an annual one with 5 days
                # left are the same problem, and both are about to become $0.
                "urgent": (not is_used) and left <= 7,
            })
            if is_used:
                captured += amount
            else:
                available += amount
                if left <= 7:
                    at_risk += amount

    rows.sort(key=lambda r: (r["used"], r["days_left"], -r["amount"]))
    return {
        "today": today.isoformat(),
        "rows": rows,
        # Money you lose if you do nothing in the next week. This is the number
        # the dashboard leads with.
        "at_risk": round(at_risk, 2),
        "available": round(available, 2),
        "captured_this_period": round(captured, 2),
        "annual_fees": round(sum(CARDS[c]["annual_fee"] for c in cards if c in CARDS), 2),
        "annual_credit_value": round(
            sum(cr["annual_max"] for c in cards if c in CARDS
                for cr in CARDS[c]["credits"]), 2),
        "catalogue_checked": CATALOGUE_CHECKED,
    }


def captured_ytd(year, redemptions, cards=("platinum", "gold")):
    """What the cards have actually returned in `year`, against their fees.

    The honest version of "is this card worth keeping": not the advertised
    value of the credits, but the ones marked used. `redemptions` is a list of
    {credit_key, period_id, amount} — amount may be None, meaning the full
    credit was drawn.

    Deliberately NOT inferred from the catalogue: a credit retired since it was
    marked is skipped rather than back-filled, and a credit belonging to a card
    outside `cards` does not leak into that card's total.
    """
    per_card = {c: 0.0 for c in cards if c in CARDS}
    counted = 0
    for row in redemptions:
        if not str(row.get("period_id", "")).startswith(f"{year}-"):
            continue
        credit = BY_KEY.get(row.get("credit_key"))
        if not credit or credit["card"] not in per_card:
            continue
        # An explicit amount wins: a $25 credit you only drew $9 of is $9.
        amount = row.get("amount")
        per_card[credit["card"]] += float(amount) if amount is not None else credit["amount"]
        counted += 1

    fees = sum(CARDS[c]["annual_fee"] for c in per_card)
    total = sum(per_card.values())
    return {
        "year": year,
        "captured": round(total, 2),
        "per_card": {k: round(v, 2) for k, v in per_card.items()},
        "annual_fees": round(fees, 2),
        "net": round(total - fees, 2),
        "counted": counted,
    }
