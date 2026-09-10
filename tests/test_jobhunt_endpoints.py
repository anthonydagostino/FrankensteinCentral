"""core's /jobhunt endpoints against a real PostgreSQL (SCRUM-131).

Everything else about this change is unit-tested without a database: which
keys are allowed, what the page does with a leftover local copy, what the
saved stamp may claim. The SQL is the one part none of that touches, and it
is exactly where a green suite hides a broken feature — an ANY(%s) that does
not bind, an ON CONFLICT against the wrong constraint, a transaction that
never commits. So this runs the real handlers, through the real pool, against
a throwaway cluster from the same harness tests/test_backup_restore.py uses.

The design argument is the last test: writes are per KEY. Two devices editing
different companies must both land, which a whole-document PUT could not do.

Skips where PostgreSQL binaries or psycopg are absent, like the backup test.
All fixture data is synthetic — the repo is public.
"""
import asyncio
import importlib
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conftest import load_service_module  # noqa: E402
from test_backup_restore import Cluster, needs_pg  # noqa: E402  (sibling)

pytest.importorskip("psycopg_pool")
httpx = pytest.importorskip("httpx")


@pytest.fixture(scope="module")
def pg():
    d = tempfile.mkdtemp(prefix="fcpg.", dir="/tmp")
    c = Cluster(d)
    try:
        c.start()
        yield c
    finally:
        c.stop()
        shutil.rmtree(d, ignore_errors=True)


_n = 0


def _evict_psycopg_stubs(monkeypatch):
    """Bind core's main.py to the REAL driver, whatever ran before us.

    Some service suites plant a fake `psycopg` in sys.modules so a main.py can
    be imported without a database, and the fake outlives the test that made
    it. In the full run that fake shadowed the real package here and main.py
    failed on `from psycopg.rows import tuple_row` — eight errors that did not
    reproduce with this file alone. A stub has no __file__; the real package
    always does. Evict stubs only, through monkeypatch, so they are put back
    after this test and the suite that relies on them is unaffected.
    """
    for name in list(sys.modules):
        if name.split(".")[0] in ("psycopg", "psycopg_pool") \
                and getattr(sys.modules[name], "__file__", None) is None:
            monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.import_module("psycopg.rows")
    importlib.import_module("psycopg_pool")


@pytest.fixture
def core(pg, monkeypatch):
    """A fresh import of core's main.py per test.

    The pool is created at import and bound to the loop it opens in, and a
    closed psycopg pool cannot be reopened — so every test gets its own
    module (unique alias) and runs startup, requests and shutdown inside one
    asyncio.run().
    """
    global _n
    _n += 1
    _evict_psycopg_stubs(monkeypatch)
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql://{pg.user}@/{pg.db}?host={pg.sock}&port={pg.port}")
    mod = load_service_module(f"core_main_it_{_n}", "services/core/app/main.py")
    pg.psql("DROP TABLE IF EXISTS jobhunt")   # every test starts empty
    return mod


def run(core, scenario):
    """startup -> scenario(client) -> shutdown, on one event loop."""
    async def go():
        await core.startup()
        try:
            transport = httpx.ASGITransport(app=core.app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://core") as c:
                return await scenario(c)
        finally:
            await core.shutdown()
    return asyncio.run(go())


@needs_pg
def test_an_empty_table_is_an_honest_empty_answer(core):
    """"Nothing saved yet" is a real state, distinct from an error."""
    async def s(c):
        r = await c.get("/jobhunt")
        assert r.status_code == 200
        assert r.json() == {"entries": {}, "count": 0, "updated_at": None}
    run(core, s)


@needs_pg
def test_put_then_get_round_trips_every_field(core):
    async def s(c):
        body = {"entries": {"jobhunt_rank_weight_pay": "3",
                            "jobhunt_acme_pros": '["remote", "401k"]',
                            "jobhunt_acme_notes": "recruiter said Thursday"}}
        r = await c.put("/jobhunt", json=body)
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "saved": 3, "removed": 0}
        g = (await c.get("/jobhunt")).json()
        assert g["entries"] == body["entries"]
        assert g["count"] == 3
        assert g["updated_at"] is not None
    run(core, s)


@needs_pg
def test_null_deletes_and_an_empty_string_is_kept(core):
    """A reset is a delete the server sees; clearing the notes box is an edit
    that must survive as "no notes", not snap back to the default."""
    async def s(c):
        await c.put("/jobhunt", json={"entries": {"jobhunt_acme_pros": "x",
                                                  "jobhunt_acme_notes": "y"}})
        r = await c.put("/jobhunt", json={"entries": {"jobhunt_acme_pros": None,
                                                      "jobhunt_acme_notes": ""}})
        assert r.json() == {"ok": True, "saved": 1, "removed": 1}
        assert (await c.get("/jobhunt")).json()["entries"] == {"jobhunt_acme_notes": ""}
    run(core, s)


@needs_pg
def test_an_upsert_overwrites_in_place(core):
    async def s(c):
        await c.put("/jobhunt", json={"entries": {"jobhunt_acme_floor": "100000"}})
        t1 = (await c.get("/jobhunt")).json()["updated_at"]
        await c.put("/jobhunt", json={"entries": {"jobhunt_acme_floor": "120000"}})
        g = (await c.get("/jobhunt")).json()
        assert g["entries"] == {"jobhunt_acme_floor": "120000"}
        assert g["count"] == 1, "an overwrite grew the table"
        assert g["updated_at"] >= t1
    run(core, s)


@needs_pg
def test_one_bad_key_is_refused_with_400_and_nothing_is_written(core):
    """The page must never be told "saved" about half a batch."""
    async def s(c):
        r = await c.put("/jobhunt", json={"entries": {"jobhunt_acme_pros": "fine",
                                                      "cc_theme": "not ours"}})
        assert r.status_code == 400
        assert "namespace" in r.json()["detail"]
        assert (await c.get("/jobhunt")).json()["count"] == 0
    run(core, s)


@needs_pg
def test_a_non_string_value_is_refused_by_the_model(core):
    async def s(c):
        r = await c.put("/jobhunt", json={"entries": {"jobhunt_acme_floor": 100000}})
        assert r.status_code == 422
        assert (await c.get("/jobhunt")).json()["count"] == 0
    run(core, s)


@needs_pg
def test_two_devices_editing_different_companies_both_land(core):
    """THE DESIGN ARGUMENT. Per-key storage: the phone saving one company's
    notes and the laptop saving another's must not clobber each other, which
    is exactly what a whole-document PUT would have done."""
    async def s(c):
        await c.put("/jobhunt", json={"entries": {"jobhunt_acme_notes": "from the phone"}})
        await c.put("/jobhunt", json={"entries": {"jobhunt_globex_notes": "from the laptop"}})
        g = (await c.get("/jobhunt")).json()["entries"]
        assert g == {"jobhunt_acme_notes": "from the phone",
                     "jobhunt_globex_notes": "from the laptop"}
    run(core, s)


@needs_pg
def test_the_table_survives_a_restart_because_it_is_in_the_database(core, pg):
    """The whole point of the ticket: this used to live in one browser."""
    async def s(c):
        await c.put("/jobhunt", json={"entries": {"jobhunt_acme_notes": "kept"}})
    run(core, s)
    # A second, independent process of the service — the SQL, not the pool —
    # is what holds it.
    assert pg.psql("SELECT value FROM jobhunt WHERE key='jobhunt_acme_notes'").stdout.strip() == "kept"
