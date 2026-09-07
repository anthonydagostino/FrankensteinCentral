"""The page-cap boundary: when completeness genuinely cannot be proven.

The cap is a resource limit, not a fact about the ledger. Three cases decide
whether a read may be called complete, and the middle one is the subtle one:

  short final page   -> the ledger ended inside the window. COMPLETE.
  exactly cap*50     -> the last page read was FULL and the cap is spent, so
                        the next page might hold one more row or none. The
                        honest answer is that completeness cannot be proven,
                        so it must NOT be claimed. INCOMPLETE.
  more than cap*50   -> plainly truncated. INCOMPLETE.

Getting the middle case wrong is silent: the totals look ordinary and are
simply short, which understates spending and overstates "left to spend".

No asyncio.run here, deliberately. It closes the loop it creates and clears
the thread's current event loop on exit; services/gmail/tests/test_sync.py
calls get_event_loop(), so an unrelated suite fails depending only on file
collection order. The loop is saved and restored instead.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

ff = load_service_module("firefly_main", "services/firefly/app/main.py")

PAGE = 50  # what _fetch_txns treats as a full page


def run_async(coro):
    """Run a coroutine without disturbing the thread's current event loop."""
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(previous)


class _Response:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": self._rows}


class _Client:
    """Serves `total` transactions, 50 to a page, counting the pages asked for."""

    def __init__(self, total):
        self.total = total
        self.pages_served = 0

    async def get(self, url, params=None, headers=None, timeout=None):
        page = int((params or {}).get("page", 1))
        self.pages_served = max(self.pages_served, page)
        start = (page - 1) * PAGE
        rows = []
        for i in range(start, min(start + PAGE, self.total)):
            rows.append({"id": str(i), "attributes": {
                "created_at": "2026-09-01T00:00:00-04:00",
                "updated_at": "2026-09-01T00:00:00-04:00",
                "transactions": [{"description": f"t{i}", "amount": "1.00",
                                  "date": "2026-09-01T12:00:00-04:00",
                                  "type": "withdrawal", "category_name": None,
                                  "currency_code": "USD", "source_name": "Checking",
                                  "destination_name": "Store"}]}})
        return _Response(rows)


def fetch(total, max_pages):
    client = _Client(total)
    rows = run_async(ff._fetch_txns(client, "withdrawal", "2026-09-01",
                                    "2026-09-30", max_pages=max_pages))
    return rows, client


def test_a_short_final_page_proves_the_read_is_complete():
    rows, client = fetch(total=PAGE * 2 - 7, max_pages=6)
    assert len(rows) == PAGE * 2 - 7
    assert rows.complete is True
    assert client.pages_served == 2      # stopped on the short page


def test_exactly_cap_times_50_cannot_be_proven_complete():
    """THE BOUNDARY. Every page read was full and the cap is spent, so there
    is no evidence either way — and "no evidence" must not read as complete."""
    cap = 4
    rows, client = fetch(total=PAGE * cap, max_pages=cap)
    assert len(rows) == PAGE * cap        # every row that exists was read...
    assert rows.complete is False         # ...but that cannot be established
    assert client.pages_served == cap


def test_more_than_the_cap_is_incomplete():
    cap = 4
    rows, _ = fetch(total=PAGE * cap + 1, max_pages=cap)
    assert len(rows) == PAGE * cap
    assert rows.complete is False


def test_one_short_of_the_boundary_is_complete():
    """The control on the other side of the line: the final page is short by
    one, which is proof the ledger ended."""
    cap = 4
    rows, _ = fetch(total=PAGE * cap - 1, max_pages=cap)
    assert len(rows) == PAGE * cap - 1
    assert rows.complete is True


def test_an_empty_window_is_complete_not_unknown():
    rows, _ = fetch(total=0, max_pages=6)
    assert list(rows) == []
    assert rows.complete is True


@pytest.mark.parametrize("cap", [1, 2, 6, 10])
def test_the_boundary_holds_at_every_cap(cap):
    assert fetch(total=PAGE * cap, max_pages=cap)[0].complete is False
    assert fetch(total=PAGE * cap - 1, max_pages=cap)[0].complete is True
