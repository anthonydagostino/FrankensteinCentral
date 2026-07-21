import os
from datetime import date, datetime, timedelta

from fastapi import FastAPI
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

app = FastAPI(title="Net Worth Service")

DATABASE_URL = os.environ["DATABASE_URL"]
pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    balance    NUMERIC NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recurring (
    id            SERIAL PRIMARY KEY,
    account_id    INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    amount        NUMERIC NOT NULL,
    interval_days INTEGER NOT NULL DEFAULT 14,
    next_due_at   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
"""

# Your real accounts — seeded once, the first time this service ever runs.
# Never re-seeded on restart, so if you delete one it stays gone.
SEED_ACCOUNTS = ["Chase Checking", "Marcus HYSA", "Robinhood", "Fidelity", "TSP"]


class Account(BaseModel):
    name: str
    balance: float = 0


class BalanceUpdate(BaseModel):
    balance: float


class Recurring(BaseModel):
    account_id: int
    amount: float
    interval_days: int = 14
    start_at: str | None = None  # ISO date; defaults to today


@app.on_event("startup")
async def startup():
    await pool.open(wait=True, timeout=30)
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'accounts')"
        )
        (already_existed,) = await cur.fetchone()
        await conn.execute(SCHEMA)
        if not already_existed:
            for name in SEED_ACCOUNTS:
                await conn.execute(
                    "INSERT INTO accounts (name, balance, updated_at) VALUES (%s, 0, %s)",
                    (name, datetime.utcnow().isoformat()),
                )


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


async def _accounts() -> list[dict]:
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT id, name, balance, updated_at FROM accounts ORDER BY id"
        )
        return await cur.fetchall()


@app.get("/health")
async def health():
    rows = await _accounts()
    total = sum(float(r["balance"]) for r in rows)
    return {"service": "networth", "accounts": len(rows), "total": round(total, 2)}


@app.get("/accounts")
async def get_accounts():
    rows = await _accounts()
    return {"accounts": rows, "count": len(rows)}


@app.post("/accounts")
async def add_account(account: Account):
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute(
            "INSERT INTO accounts (name, balance, updated_at) VALUES (%s, %s, %s) RETURNING id",
            (account.name, account.balance, datetime.utcnow().isoformat()),
        )
        (new_id,) = await cur.fetchone()
    return {"added": {"id": new_id, "name": account.name, "balance": account.balance}}


@app.post("/accounts/{account_id}/balance")
async def set_balance(account_id: int, update: BalanceUpdate):
    """Set an account's current balance — you sync this manually since these
    are real external accounts (bank, brokerage, TSP), not API-connected."""
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "UPDATE accounts SET balance = %s, updated_at = %s WHERE id = %s "
            "RETURNING id, name, balance",
            (update.balance, datetime.utcnow().isoformat(), account_id),
        )
        row = await cur.fetchone()
    return {"account": row}


@app.delete("/accounts/{account_id}")
async def delete_account(account_id: int):
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM accounts WHERE id = %s", (account_id,))
    return {"deleted": account_id}


@app.get("/recurring")
async def get_recurring():
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT r.id, r.account_id, a.name AS account, r.amount, "
            "r.interval_days, r.next_due_at "
            "FROM recurring r JOIN accounts a ON a.id = r.account_id "
            "ORDER BY r.next_due_at"
        )
        rows = await cur.fetchall()
    return {"recurring": rows, "count": len(rows)}


@app.post("/recurring")
async def add_recurring(rule: Recurring):
    start = rule.start_at or date.today().isoformat()
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute(
            "INSERT INTO recurring (account_id, amount, interval_days, next_due_at, created_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (rule.account_id, rule.amount, rule.interval_days, start, datetime.utcnow().isoformat()),
        )
        (new_id,) = await cur.fetchone()
    return {"added": {"id": new_id, "account_id": rule.account_id, "amount": rule.amount,
                       "interval_days": rule.interval_days, "next_due_at": start}}


@app.delete("/recurring/{rule_id}")
async def delete_recurring(rule_id: int):
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM recurring WHERE id = %s", (rule_id,))
    return {"deleted": rule_id}


@app.post("/recurring/apply")
async def apply_recurring():
    """Apply every recurring contribution that's come due, catching up on
    any that were missed (e.g. the box was off for a while)."""
    today = date.today().isoformat()
    applied = []
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute("SELECT * FROM recurring")
        rules = await cur.fetchall()
        for r in rules:
            next_due = r["next_due_at"]
            while next_due <= today:
                await conn.execute(
                    "UPDATE accounts SET balance = balance + %s, updated_at = %s WHERE id = %s",
                    (r["amount"], datetime.utcnow().isoformat(), r["account_id"]),
                )
                acct_cur = await conn.execute(
                    "SELECT name, balance FROM accounts WHERE id = %s", (r["account_id"],)
                )
                acct = await acct_cur.fetchone()
                applied.append({
                    "account": acct["name"] if acct else None,
                    "amount": float(r["amount"]),
                    "new_balance": float(acct["balance"]) if acct else None,
                    "applied_on": next_due,
                })
                next_due = (date.fromisoformat(next_due) + timedelta(days=r["interval_days"])).isoformat()
            if next_due != r["next_due_at"]:
                await conn.execute(
                    "UPDATE recurring SET next_due_at = %s WHERE id = %s", (next_due, r["id"])
                )
    return {"applied": applied}


@app.get("/summary")
async def summary():
    rows = await _accounts()
    total = sum(float(r["balance"]) for r in rows)
    return {
        "total": round(total, 2),
        "accounts": [{"name": r["name"], "balance": float(r["balance"])} for r in rows],
        "count": len(rows),
    }


@app.get("/")
async def root():
    return {
        "app": "Net Worth",
        "endpoints": ["/accounts", "/recurring", "/recurring/apply", "/summary", "/health"],
    }
