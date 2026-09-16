"""Which calendar rows this app invented, and which are the user's own.

Its own module, with no database import, so the rule that decides what gets
DELETED from a real Google Calendar can be tested directly — the same reason
`dashboard.py` and `credits.py` are separate from the services that serve
them.
"""


def is_speculative_hold(row) -> bool:
    """Is this row a time we OFFERED, rather than an entry on the calendar?

    The rule that decides what gets deleted from a real Google Calendar, so it
    is a function with tests and not a WHERE clause. All three conditions have
    to hold, and each one rules out a different thing that must survive:

      source == 'gmail'        `sync_from_calendar` imports Google's own events
                               as 'google_calendar'. THIS is the condition that
                               stops a purge reaching the user's real calendar.
      status is pending/countered
                               A confirmed interview is a commitment.
      ':slot:' in external_id  The exact shape the assistant minted per proposed
                               slot (`<thread>:slot:<time>`). A confirmed one is
                               `<thread>:confirmed`, and a manual event is
                               `manual:<uuid>`.

    THE TRAP, and the reason `source` is not redundant: Google marks an invite
    you have not accepted as "tentative", and `sync_from_calendar` stores
    anything that is not "confirmed" as status 'pending'. So a real, tentative
    entry on the user's own calendar is a pending row. Only `source` tells it
    apart from something this app invented.
    """
    if not isinstance(row, dict):
        return False
    if row.get("source") != "gmail":
        return False
    if row.get("status") not in ("pending", "countered"):
        return False
    return ":slot:" in (row.get("external_id") or "")
