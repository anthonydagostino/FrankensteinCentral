"""Job-hunt research storage rules (SCRUM-131).

`gateway/static/jobs.html` kept the weighted ranking, the per-factor scores,
the pros, cons, notes and salary floors in `localStorage`, under `jobhunt_*`
keys — and said so in its own subtitle. That put the most decision-relevant
thinking in the whole system, how the companies you might work for actually
rank, in exactly one browser and in no backup, while water intake sat in a
replicated Postgres.

It now lives in core. This module holds the two decisions that must be right
and can be tested without a database:

  * WHAT A KEY MAY LOOK LIKE. The page's key namespace is kept as-is, so the
    server stores per FIELD, not one document. Two devices editing different
    companies must not clobber each other, and a whole-document PUT would.
    The namespace is closed: anything not `jobhunt_<lower/digits/_>` is
    refused rather than stored, so the table cannot drift into a general
    key/value dump by accident.
  * WHAT COUNTS AS A DELETE. `null` removes the key. The page's reset buttons
    map onto that, so a reset is a write the server sees rather than a
    browser quietly forgetting. An EMPTY STRING is a value, not a delete:
    clearing the notes box is an edit, and it must survive a reload as "no
    notes" rather than snapping back to the pre-filled default.
"""
import re

KEY = re.compile(r"^jobhunt_[a-z0-9_]{1,120}$")
MAX_VALUE = 20_000     # a card's notes or a long pros list; matches `seen`
MAX_ENTRIES = 500      # a full import is ~50 keys; this is a sanity ceiling


def validate_entries(entries):
    """Split a PUT body into (upserts, deletes), or raise ValueError.

    Refuses the whole batch on the first bad entry. A partial write would
    leave the page believing something was saved that was not.
    """
    if not isinstance(entries, dict):
        raise ValueError("entries must be an object")
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"at most {MAX_ENTRIES} entries per write")
    upserts, deletes = {}, []
    for key, value in entries.items():
        if not isinstance(key, str) or not KEY.match(key):
            raise ValueError(f"key is not in the jobhunt namespace: {key!r}")
        if value is None:
            deletes.append(key)
            continue
        if not isinstance(value, str):
            raise ValueError(f"value for {key} must be a string or null")
        if len(value) > MAX_VALUE:
            raise ValueError(f"value for {key} exceeds {MAX_VALUE} characters")
        upserts[key] = value
    return upserts, deletes
