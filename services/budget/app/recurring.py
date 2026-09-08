"""Recurring-charge detection — pure, deterministic. No I/O.

SCRUM-19 is "run find_recurring.py on 13 months of card CSVs": a script you
run by hand, once, whose output is stale the moment it finishes. Firefly
already holds every transaction, so recurrence can be a property of the system
instead of an errand. This module is the whole of that decision, kept pure so
it can be swept across a calendar rather than tested on the day it was written.

It answers three questions the money card should be able to ask continuously:

    appeared    something started charging you that wasn't charging before
    changed     a known charge moved price
    resumed     one you believed cancelled charged again after a gap

Inputs:
  charges     [{date, desc, amount(+), destination, category}] over a long
              window — withdrawals only; transfers are not purchases
  today       ISO date or date
  window_start the FIRST day the caller actually read. Not decoration: the
              difference between "this is new" and "this is the oldest thing
              I can see" is entirely this value.
  known_bills [{name, amount}] already declared in Firefly, so a bill the
              user has told the system about is never announced as a discovery
  complete    False when the read was truncated (see docs/BUDGETS.md)

WHAT IT REFUSES TO CLAIM, which is most of the design:

  * "Appeared" requires history BEFORE it. If the first charge sits within a
    cadence of the window's own start, the only honest answer is that we
    cannot see far enough back, and it is reported as unknown rather than new.
  * "New" has a shelf life, and a cadence too slow to prove itself inside that
    shelf life can never be new. This is what closes the most common false
    positive in this class of feature — an annual renewal seen twice, called a
    brand-new subscription — and it closes it by arithmetic rather than by
    demanding a third charge, which for a monthly subscription would never
    arrive inside the window at all.
  * A truncated window suppresses "appeared" and "resumed" entirely: both are
    absence claims, and absence is exactly what a partial read cannot support.
    Price changes survive, because those are two charges we did read.
  * Amounts drift. A price change must clear both a relative and an absolute
    floor, so a $0.01 card-rounding wobble is not an announcement.
  * A missed cycle is not a disproof of the cadence — but it is also not free:
    a gap must land near a whole multiple of the rhythm to be forgiven, and
    anything else demotes the merchant to "not a pattern".
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta

# Cadences recognised, as (name, low, high, nominal) in days. The bands are
# wide because billing dates slide across weekends and month lengths.
CADENCES = (
    ("weekly", 6, 8, 7),
    ("fortnightly", 12, 16, 14),
    ("monthly", 26, 35, 30),
    ("quarterly", 82, 98, 91),
    ("annual", 350, 380, 365),
)

MIN_CHARGES_FOR_PATTERN = 2      # below this there is nothing to compare
CONFIDENT_CHARGES = 3            # two intervals before a cadence is asserted
INTERVAL_TOLERANCE = 0.35        # how far one interval may sit from the median
MAX_MISSED_CYCLES = 12           # beyond this a "gap" is a different era
RESUMED_GAP_FACTOR = 2.0         # a gap this many cadences long reads as a stop
PRICE_CHANGE_PCT = 0.02          # 2% ...
PRICE_CHANGE_MIN = 0.50          # ...and at least this many dollars
APPEARED_WINDOW_DAYS = 90        # how long "new" stays news
RESUMED_WITHIN_CADENCES = 1.5    # a resumption is only news while it is fresh

_NOISE = re.compile(r"""
      \b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b   # embedded dates
    | \b\d{4,}\b                             # order/store/card numbers
    | \*+\d+                                 # *1234 style suffixes
    | \#\s*\d+                               # #123
    | \s{2,}
""", re.VERBOSE)


def _as_date(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _amount(t: dict) -> float:
    try:
        return abs(float(t.get("amount") or 0))
    except (TypeError, ValueError):
        return 0.0


def _norm_name(v) -> str:
    return " ".join(str(v or "").lower().split())


def merchant_key(t: dict) -> str:
    """Collapse one charge to the thing that recurs.

    Statement text carries per-charge noise — dates, order numbers, store
    branches — that would split one subscription into many singletons and
    hide every pattern. The destination account is preferred when Firefly has
    one, since it is already normalised; otherwise the description is stripped
    and lowercased. Deliberately conservative: over-merging invents patterns
    that aren't there, which is worse than missing one.
    """
    dest = str(t.get("destination") or "").strip()
    raw = dest or str(t.get("desc") or "")
    cleaned = _NOISE.sub(" ", raw.lower())
    cleaned = re.sub(r"[^a-z0-9&. ]+", " ", cleaned)
    return " ".join(cleaned.split())[:48]


def display_name(t: dict) -> str:
    """What to call this merchant on screen.

    Firefly's destination account is already a human name, so it wins. A bare
    description is statement text — stripped of its per-charge noise, but not
    re-capitalised, because guessing at capitalisation of an unknown merchant
    reads worse than showing what the bank actually sent.
    """
    dest = str(t.get("destination") or "").strip()
    if dest:
        return dest
    raw = str(t.get("desc") or "")
    cleaned = " ".join(_NOISE.sub(" ", raw).split())
    return cleaned or raw.strip()


def _near(value: int, target: float, tolerance: float) -> bool:
    return abs(value - target) <= tolerance


def _classify_cadence(intervals: list[int]) -> tuple[str | None, int | None]:
    """The cadence these gaps describe, or None when they describe nothing.

    The median sets the rhythm. Every other interval must then be either that
    rhythm or a whole number of it — a subscription you skipped a month of is
    still that subscription, and refusing to say so is what would make
    "resumed" impossible to ever detect. An interval that is neither (three
    days, when the rhythm is thirty) means this is a merchant you happen to
    use often, not a commitment, and it is dropped rather than smoothed over.
    """
    if not intervals:
        return None, None
    ordered = sorted(intervals)
    median = ordered[len(ordered) // 2]
    if median <= 0:
        return None, None
    tolerance = max(3.0, median * INTERVAL_TOLERANCE)
    for i in intervals:
        if _near(i, median, tolerance):
            continue
        cycles = round(i / median)
        if 2 <= cycles <= MAX_MISSED_CYCLES and _near(i, cycles * median,
                                                      tolerance * cycles):
            continue
        return None, None
    # No "the rhythm must be the majority" rule is needed here, and one would
    # be unreachable: the median sits at index len//2, so more than half the
    # intervals are at or below it, and any interval below the median that is
    # not near it has already returned above — a sub-multiple never rounds to
    # two or more cycles. Reaching this line means the rhythm IS the majority.
    for name, low, high, nominal in CADENCES:
        if low <= median <= high:
            return name, nominal
    return None, None


def _established_amount(amounts: list[float]) -> float:
    """The amount this charge 'is' — the most common, ties going to the most
    recent, so a one-off promo month doesn't redefine the price."""
    counts = Counter(round(a, 2) for a in amounts)
    top = max(counts.values())
    candidates = [a for a, c in counts.items() if c == top]
    for a in reversed([round(x, 2) for x in amounts]):
        if a in candidates:
            return a
    return candidates[0]


def _price_moved(old: float, new: float) -> bool:
    if old <= 0:
        return False
    return abs(new - old) >= max(PRICE_CHANGE_MIN, old * PRICE_CHANGE_PCT)


def _can_be_new(nominal: int) -> bool:
    """Whether a cadence this slow could have proven itself inside the window
    in which 'new' is still news. Quarterly and annual cannot, which is
    exactly the annual-renewal-seen-twice false positive, refused by
    arithmetic instead of by a charge count a monthly sub would never meet."""
    span = nominal * (MIN_CHARGES_FOR_PATTERN - 1)
    return span <= APPEARED_WINDOW_DAYS


def detect_recurring(charges: list, today, window_start, known_bills=None,
                     complete: bool = True) -> dict:
    today = _as_date(today) or date.today()
    window_start = _as_date(window_start)

    known: set[str] = set()
    for b in known_bills or []:
        raw = b.get("name") if isinstance(b, dict) else b
        known.add(_norm_name(raw))
        known.add(merchant_key({"desc": raw}))
    known.discard("")

    groups: dict[str, list[dict]] = {}
    for t in charges or []:
        d = _as_date(t.get("date"))
        if d is None or d > today:
            continue
        if window_start and d < window_start:
            continue
        key = merchant_key(t)
        if not key:
            continue
        groups.setdefault(key, []).append({
            "date": d,
            "amount": _amount(t),
            "name": display_name(t) or key,
        })

    items, events = [], []
    for key, rows in groups.items():
        rows.sort(key=lambda r: r["date"])
        if len(rows) < MIN_CHARGES_FOR_PATTERN:
            continue
        dates = [r["date"] for r in rows]
        intervals = [(b - a).days for a, b in zip(dates, dates[1:])]
        cadence, nominal = _classify_cadence(intervals)
        if not cadence:
            continue

        amounts = [r["amount"] for r in rows]
        established = _established_amount(amounts)
        latest, last_seen, first_seen = amounts[-1], dates[-1], dates[0]
        confident = len(rows) >= CONFIDENT_CHARGES
        name = rows[-1]["name"] or key
        is_known = _norm_name(name) in known or key in known

        # --- appeared -----------------------------------------------------
        # Needs history BEFORE the first charge, or "new" means "the oldest
        # thing I can see", which is a different and much weaker claim.
        history_before = ((first_seen - window_start).days
                          if window_start else None)
        age_days = (today - first_seen).days
        if history_before is None or history_before < nominal:
            appeared = None          # cannot see far enough back
        else:
            appeared = _can_be_new(nominal) and age_days <= APPEARED_WINDOW_DAYS

        # --- resumed ------------------------------------------------------
        # A gap of two cadences or more reads as a stop; charging after it
        # reads as a restart, but only while the restart is still fresh.
        fresh = nominal * RESUMED_WITHIN_CADENCES
        gaps = [g for g in intervals if g >= nominal * RESUMED_GAP_FACTOR]
        resumed = bool(gaps) and (today - last_seen).days <= fresh

        # --- price change -------------------------------------------------
        prior = amounts[:-1]
        prior_established = _established_amount(prior) if prior else None
        changed = (prior_established is not None
                   and _price_moved(prior_established, latest)
                   and round(latest, 2) != round(prior_established, 2))

        item = {
            "key": key, "name": name,
            "cadence": cadence, "cadence_days": nominal,
            # What it costs now, and what it has settled at. After a price
            # change these differ, and the difference is the whole story.
            "amount": round(latest if changed else established, 2),
            "established_amount": round(established, 2),
            "latest_amount": round(latest, 2),
            "charges": len(rows),
            "first_seen": first_seen.isoformat(),
            "last_seen": last_seen.isoformat(),
            "next_expected": (last_seen + timedelta(days=nominal)).isoformat(),
            "confidence": "high" if confident else "low",
            "known_bill": is_known,
            "history_before_days": history_before,
        }
        items.append(item)

        # An absence claim from a partial read is not a claim. Price changes
        # are two charges we actually read, so they survive.
        if changed:
            events.append({**item, "event": "changed",
                           "from": round(prior_established, 2),
                           "to": round(latest, 2)})
        if not complete:
            continue
        if appeared and not is_known:
            events.append({**item, "event": "appeared"})
        elif resumed:
            events.append({**item, "event": "resumed",
                           "gap_days": max(gaps)})

    items.sort(key=lambda i: (-i["amount"], i["name"]))
    order = {"appeared": 0, "changed": 1, "resumed": 2}
    events.sort(key=lambda e: (order.get(e["event"], 9), -e["amount"], e["name"]))
    # What these commitments cost per month, normalised across cadences.
    # Low-confidence items are excluded rather than estimated.
    monthly = round(sum(i["amount"] * (30 / i["cadence_days"])
                        for i in items if i["confidence"] == "high"), 2)
    return {
        "available": True,
        "window_complete": complete,
        "window_start": window_start.isoformat() if window_start else None,
        "items": items,
        "events": events,
        "monthly_equivalent": monthly,
        # Absence claims were suppressed, so say so instead of implying none.
        "absence_claims_suppressed": not complete,
    }
