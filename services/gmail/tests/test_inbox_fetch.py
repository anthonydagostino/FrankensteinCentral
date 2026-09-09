"""The inbox walk itself — the one part of this service that talks to Google.

WHY THIS FILE EXISTS: every existing gmail test monkeypatches `_fetch_inbox`
away, which is right for testing what the service does with mail and left the
fetch itself with no coverage at all. It has real behaviour to get wrong:
Gmail has no bulk "give me these messages" endpoint, so a list of ids costs one
request each, and those requests were made one after another — 25 serial round
trips to Google on every background sync, the slowest thing this service does.

They now go out concurrently, bounded. That is only worth having if it is
actually concurrent and actually bounded, and only safe if the results still
line up with the ids that asked for them — so all three are asserted here
rather than assumed from the shape of the code.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from conftest import load_service_module  # noqa: E402

gm = load_service_module("gmail_main", "services/gmail/app/main.py")


class FakeGmail:
    """Enough of Gmail to answer a list and per-message metadata reads.

    Records how many requests are in flight at once, which is the only way to
    tell a concurrent walk from a serial one from the outside.
    """

    def __init__(self, ids, *, fail=(), delay=0.01, thread_of=None):
        self.ids = ids
        self.fail = set(fail)
        self.delay = delay
        self.thread_of = thread_of or {}
        self.in_flight = 0
        self.peak = 0
        self.asked = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None, timeout=None):
        if url.endswith("/messages"):
            return _Response(200, {"messages": [{"id": i} for i in self.ids]})

        mid = url.rsplit("/", 1)[-1]
        self.asked.append(mid)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            # A real network read yields; without this every request would
            # complete before the next one started and the test could not tell
            # concurrent from serial.
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        if mid in self.fail:
            return _Response(404, {})
        return _Response(200, {
            "id": mid,
            "threadId": self.thread_of.get(mid, mid),
            "snippet": f"snippet {mid}",
            "internalDate": "1700000000000",
            "labelIds": ["INBOX"],
            "payload": {"headers": [
                {"name": "From", "value": f"{mid}@example.com"},
                {"name": "Subject", "value": f"subject {mid}"},
            ]},
        })


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def gmail(monkeypatch):
    def install(fake):
        monkeypatch.setattr(gm, "_access_token", lambda: _async("token"))
        monkeypatch.setattr(gm.httpx, "AsyncClient", lambda *a, **k: fake)
        return fake
    return install


async def _async(value):
    return value


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_every_message_comes_back_in_the_order_it_was_asked_for(gmail):
    """gather preserves argument order; a walk that returned them by whichever
    reply landed first would shuffle the inbox on every sync."""
    fake = gmail(FakeGmail([f"m{i}" for i in range(10)]))
    out = run(gm._fetch_inbox())
    assert [m["id"] for m in out] == fake.ids
    assert [m["subject"] for m in out] == [f"subject m{i}" for i in range(10)]


def test_the_walk_is_actually_concurrent(gmail):
    """The whole point. Serially this is 10 round trips end to end."""
    fake = gmail(FakeGmail([f"m{i}" for i in range(10)]))
    run(gm._fetch_inbox())
    assert fake.peak > 1, "the messages were still fetched one at a time"


def test_it_never_opens_more_sockets_than_the_cap(gmail):
    """Unbounded, a large inbox would open a socket per message and walk into
    Gmail's per-user rate limit. 25 ids, cap of 10."""
    fake = gmail(FakeGmail([f"m{i}" for i in range(25)]))
    run(gm._fetch_inbox())
    assert fake.peak <= gm._MAX_IN_FLIGHT, fake.peak
    assert len(fake.asked) == 25


def test_one_bad_message_is_skipped_not_fatal(gmail):
    """Exactly what the sequential loop's `continue` did. A single message
    Google will not hand over must not cost the whole sync."""
    gmail(FakeGmail([f"m{i}" for i in range(6)], fail={"m2", "m4"}))
    out = run(gm._fetch_inbox())
    assert [m["id"] for m in out] == ["m0", "m1", "m3", "m5"]


def test_an_exception_mid_walk_is_also_survivable(gmail, monkeypatch):
    """A dropped connection raises rather than returning a status code, and
    gather would otherwise propagate it and lose all 25 messages."""
    fake = FakeGmail([f"m{i}" for i in range(4)])
    original = fake.get

    async def flaky(url, **kw):
        if url.endswith("m1"):
            raise ConnectionError("reset")
        return await original(url, **kw)

    fake.get = flaky
    gmail(fake)
    out = run(gm._fetch_inbox())
    assert [m["id"] for m in out] == ["m0", "m2", "m3"]


def test_the_fields_the_rest_of_the_service_reads_are_all_present(gmail):
    """The walk feeds classification and the dashboard; a renamed key here is
    a silently empty inbox card rather than an error."""
    gmail(FakeGmail(["m1"]))
    (msg,) = run(gm._fetch_inbox())
    assert set(msg) >= {"id", "thread_id", "from", "subject", "snippet",
                        "received", "age_hours", "list_unsubscribe",
                        "auto_submitted", "precedence", "reply_to", "labels"}
    assert msg["thread_id"] == "m1" and msg["from"] == "m1@example.com"


def test_a_failed_listing_still_reports_nothing_rather_than_empty(gmail):
    """None and [] are different answers: [] is "your inbox is clear"."""
    fake = FakeGmail([])

    async def refuse(url, **kw):
        return _Response(500, {})

    fake.get = refuse
    gmail(fake)
    assert run(gm._fetch_inbox()) is None
