"""Core service — the settings store.

One JSONB row: the holdings the stocks service prices, the budgets and pay
cycle the budget service computes from, the place the weather service reports
on, and which Firefly accounts are not spendable cash for the runway. Every
other service reads it; the ⚙ modal on the home screen writes it.

Until 2026-10-06 this was also the habit tracker and daily-score engine —
study timer, water, nutrition, sleep, gym (read from fitness), open tasks (read
from tasks), Big 3, quick capture, snooze/dismiss, the "seen" baseline and a
weekly review. All of that fed home-screen cards that were removed at
Anthony's instruction, so it went with them. The tables those endpoints wrote
(daily_log, focus_sessions, big3, captures, dismissals, seen) are untouched in
Postgres; nothing reads or writes them any more.

Nothing here is secret; no credentials are stored or returned.
"""
import json
import os

from fastapi import FastAPI
from psycopg.rows import tuple_row
from psycopg_pool import AsyncConnectionPool

app = FastAPI(title="Core Service")

DATABASE_URL = os.environ["DATABASE_URL"]

pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS core_settings (
    id   INTEGER PRIMARY KEY DEFAULT 1,
    data JSONB NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "morning_end_hour": 12,      # local hour before which = morning mode
    "evening_start_hour": 18,    # local hour at/after which = evening mode
    "market": {"holdings": [], "watchlist": [], "move_threshold_pct": 3.0},
    # The place the weather pill reports on. Coordinates rather than a name,
    # because a name has to be resolved by somebody and doing it once — when
    # you pick the place — beats doing it on every request forever. `lat`/`lon`
    # of None means nothing has been chosen, which the pill says out loud
    # instead of quietly defaulting you to a city you have never lived in.
    "weather": {"place": None, "lat": None, "lon": None,
                "timezone": None, "unit": "fahrenheit"},
    # `not_spendable` names the accounts that are NOT part of the cash pot,
    # for cash runway. It exists because Firefly cannot express the fact: its
    # whole account_role vocabulary is defaultAsset/sharedAsset/savingAsset/
    # ccAsset/cashWalletAsset — four cash-like values and a credit card, with
    # NO role meaning brokerage, investment or retirement. So a TSP and a
    # checking account are both defaultAsset and no rule can separate them.
    # Empty means "nobody has said", which the runway reports as a range
    # rather than resolving by guesswork. See docs/BUDGETS.md (SCRUM-137).
    "finance": {"not_spendable": []},
    # Monthly budgets over Firefly categories. Each: {id, name, limit,
    # categories: [firefly category names]}. The budget service computes
    # spend/pace/warnings from these; nothing else is stored.
    "budgets": [],
    # The pay cycle: which deposits are a paycheck, and the savings that come
    # out of it before the rest is spendable. The budget service turns this
    # plus Firefly's ledger into "left to spend this paycheck"; no amounts
    # are stored anywhere else.
    #   match            deposit description/account terms, case-insensitive
    #   min_amount       deposits below this are never a paycheck
    #   cadence_days     fallback when the ledger shows only one paycheck
    #   allocations      money that leaves the spendable pot on payday.
    #                    already_withheld: the employer takes it before the
    #                    deposit lands, so it is shown but NOT subtracted.
    "paycheck": {
        "enabled": True,
        "match": ["payroll", "paycheck", "direct dep", "salary"],
        "min_amount": 500,
        "cadence_days": 14,
        "allocations": [
            {"name": "Fidelity", "amount": 1100, "match": ["fidelity"],
             "already_withheld": False},
            {"name": "Marcus", "amount": 500, "match": ["marcus"],
             "already_withheld": False},
        ],
    },
}


@app.on_event("startup")
async def startup():
    await pool.open(wait=True, timeout=30)
    async with pool.connection() as conn:
        await conn.execute(SCHEMA)
        await conn.execute(
            "INSERT INTO core_settings (id, data) VALUES (1, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (json.dumps(DEFAULT_SETTINGS),),
        )


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


async def _settings() -> dict:
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute("SELECT data FROM core_settings WHERE id = 1")
        row = await cur.fetchone()
    data = dict(DEFAULT_SETTINGS)
    if row and row[0]:
        # Shallow-merge stored over defaults so new keys always exist. Keys a
        # previous version stored (study goals, score weights) ride along
        # harmlessly; nothing reads them.
        for k, v in row[0].items():
            data[k] = v
    return data


@app.get("/health")
async def health():
    return {"service": "core"}


@app.get("/settings")
async def get_settings():
    return await _settings()


@app.put("/settings")
async def put_settings(patch: dict):
    """Shallow-merge a partial settings update."""
    cur = await _settings()
    cur.update(patch or {})
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO core_settings (id, data) VALUES (1, %s) "
            "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
            (json.dumps(cur),),
        )
    return cur


@app.get("/")
async def root():
    return {"app": "Core", "endpoints": ["/settings", "/health"]}
