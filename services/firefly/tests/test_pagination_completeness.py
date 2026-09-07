"""A partial read must never be published as a complete one.

PO_REVIEW_e7adf83 finding 3 (P2): `_fetch_txns` stopped at its page cap and
returned the rows it had, indistinguishable from having read the whole
window. A long ledger therefore undercounted spending — or missed the
paycheck that anchors the pay cycle — while the card went on printing a
confident total and a $/day figure.

docs/BUDGETS.md: never present a partial window as a complete one.

All fixture data is synthetic (public repo).
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

ff = load_service_module("firefly_pagination", "services/firefly/app/main.py")


def _row(i):
    return {"id": str(i), "attributes": {
        "created_at": "2026-08-28T06:00:00-04:00",
        "transactions": [{"description": f"Synthetic {i}", "amount": "10.00",
                          "date": "2026-08-25T12:00:00-04:00",
                          "type": "withdrawal", "category_name": "Groceries",
                          "source_name": "Checking", "destination_name": "Store"}]}}


class Pages:
    """A ledger of `total` rows served 50 to a page, like Firefly."""

    def __init__(self, total):
        self.total = total
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                q = parse_qs(urlparse(self.path).query)
                page = int((q.get("page") or ["1"])[0])
                lo = (page - 1) * 50
                rows = [_row(i) for i in range(lo, min(lo + 50, outer.total))]
                body = json.dumps({"data": rows}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


@pytest.fixture
def ledger(monkeypatch):
    made = []

    def build(total):
        p = Pages(total)
        made.append(p)
        monkeypatch.setattr(ff, "FIREFLY_URL", f"http://127.0.0.1:{p.port}")
        # Synthetic, matching the existing suite's stub. Never a real token.
        monkeypatch.setattr(ff, "FIREFLY_TOKEN", "test-token")
        return p

    yield build
    for p in made:
        p.stop()


def _fetch(max_pages):
    """Drive the coroutine directly: this repo's dependency set has no
    pytest-asyncio, and CI installs exactly `pytest fastapi httpx uvicorn`.

    Deliberately NOT `asyncio.run`, which clears the thread's current event
    loop on the way out. services/gmail/tests/test_sync.py calls
    `get_event_loop()`, so a bare `asyncio.run` here made eight unrelated
    tests fail depending only on file collection order. Whatever loop was
    installed before is put back.
    """
    async def go():
        async with httpx.AsyncClient() as c:
            return await ff._fetch_txns(c, "withdrawal", "2026-08-01",
                                        "2026-09-07", max_pages=max_pages)

    try:
        prev = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        prev = None
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(go())
    finally:
        loop.close()
        asyncio.set_event_loop(prev)


def test_a_window_read_in_full_is_marked_complete(ledger):
    ledger(120)                       # 3 pages, the last one short
    got = _fetch(10)
    assert len(got) == 120
    assert got.complete is True


def test_hitting_the_page_cap_marks_the_read_incomplete(ledger):
    ledger(500)                       # 10 pages available, cap at 3
    got = _fetch(3)
    assert len(got) == 150
    assert got.complete is False, "a truncated read claimed to be the whole window"


def test_an_exactly_full_last_page_within_the_cap_is_still_complete(ledger):
    """100 rows in 2 pages with a cap of 3: page 3 comes back empty, which is
    proof there is no more — not a truncation."""
    ledger(100)
    got = _fetch(3)
    assert len(got) == 100
    assert got.complete is True


def test_the_boundary_case_cannot_prove_completeness(ledger):
    """Exactly cap*50 rows: the last page read was full and the cap is spent,
    so there is no evidence either way. Claiming complete would be a guess."""
    ledger(150)
    got = _fetch(3)
    assert len(got) == 150
    assert got.complete is False


def test_an_empty_ledger_is_complete_not_truncated(ledger):
    ledger(0)
    got = _fetch(3)
    assert list(got) == []
    assert got.complete is True, "empty and unknown are different states"
