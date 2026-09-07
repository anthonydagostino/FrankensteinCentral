"""Does the ledger window we read cover the window we claim to report on?

`_fetch_txns` pages through Firefly with a page cap. When the cap runs out
before the data does, the rows it returns are a PREFIX of the window — and
they are indistinguishable, by inspection, from a complete one. Every total
computed from them ("spent this month", "$/day to payday") stays plausible
and becomes wrong, which is the failure mode docs/BUDGETS.md names directly:
a partial window must never be presented as a complete one.

So completeness is returned as its own fact rather than inferred.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

ff = load_service_module("firefly_main", "services/firefly/app/main.py")

PAGE = 50  # the page size _fetch_txns asks Firefly for


def fetch(client, **kw):
    """No pytest-asyncio in this project; the gmail suite drives coroutines
    the same way."""
    return asyncio.new_event_loop().run_until_complete(
        ff._fetch_txns(client, "withdrawal", "2026-09-01", "2026-09-30", **kw))


def group(n=1):
    """One Firefly transaction group, in the shape the parser expects."""
    return {"attributes": {"created_at": "2026-09-01T00:00:00Z",
                           "transactions": [{"description": f"txn {n}",
                                             "amount": "10.00",
                                             "date": "2026-09-01",
                                             "type": "withdrawal"}]}}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    """Serves `pages` in order and records how many were actually requested."""

    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    async def get(self, url, params=None, headers=None, timeout=None):
        page = (params or {}).get("page", 1)
        self.requested.append(page)
        return FakeResponse(self.pages[page - 1] if page <= len(self.pages)
                            else {"data": []})


def body(count, total_pages=None):
    payload = {"data": [group(i) for i in range(count)]}
    if total_pages is not None:
        payload["meta"] = {"pagination": {"total_pages": total_pages}}
    return payload


def test_hitting_the_page_cap_is_reported_as_incomplete():
    """The case Codex reproduced: more pages exist than the cap allows.

    Before this, the caller got a short list and no way to know it was short.
    """
    client = FakeClient([body(PAGE, total_pages=9) for _ in range(9)])
    rows, complete = fetch(client, max_pages=3)
    assert complete is False, "a truncated window was reported as complete"
    assert len(rows) == 3 * PAGE
    assert client.requested == [1, 2, 3], "the cap was not respected"


def test_a_window_that_fits_is_reported_as_complete():
    client = FakeClient([body(PAGE, total_pages=2), body(7, total_pages=2)])
    rows, complete = fetch(client, max_pages=10)
    assert complete is True
    assert len(rows) == PAGE + 7


def test_exactly_filling_the_cap_is_complete_when_firefly_says_so():
    """The boundary that a naive "did we use every page" check gets wrong.

    Three pages read under a cap of three, and Firefly says three is all there
    is. Reporting that as truncated would suppress guidance on a window that
    is in fact whole.
    """
    client = FakeClient([body(PAGE, total_pages=3) for _ in range(3)])
    rows, complete = fetch(client, max_pages=3)
    assert complete is True
    assert len(rows) == 3 * PAGE


def test_without_pagination_metadata_a_short_page_ends_the_window():
    """Older Firefly builds omit meta.pagination. A page shorter than the
    page size is still unambiguous evidence that the data ran out."""
    client = FakeClient([body(PAGE), body(3)])
    rows, complete = fetch(client, max_pages=10)
    assert complete is True
    assert len(rows) == PAGE + 3


def test_without_metadata_a_full_page_at_the_cap_is_not_assumed_complete():
    """The conservative half of the same case: a full last page and no
    metadata is indistinguishable from a truncated one, so it is not claimed
    as whole."""
    client = FakeClient([body(PAGE) for _ in range(4)])
    rows, complete = fetch(client, max_pages=2)
    assert complete is False
    assert len(rows) == 2 * PAGE
