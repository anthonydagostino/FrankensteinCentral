import os
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="PowerBuy Service")

# Your real PowerBuy backend (the Render API, not the Vercel frontend).
API_URL = os.environ.get("POWERBUY_API_URL", "https://powerbuy.onrender.com/api").rstrip("/")
EMAIL = os.environ.get("POWERBUY_EMAIL", "")
PASSWORD = os.environ.get("POWERBUY_PASSWORD", "")

# Cached JWT so we don't log in on every request.
_TOKEN: str | None = None

MOCK_PURCHASES = [
    {
        "item": "DeWalt 20V Drill",
        "sellPrice": 149.0,
        "profit7Percent": 22.0,
        "paymentStatus": "Unpaid",
        "deliveryStatus": "Delivered",
        "expires": "2026-07-28T00:00:00",
        "quantity": 2,
    },
    {
        "item": "Whey Isolate 5lb",
        "sellPrice": 59.0,
        "profit7Percent": 8.5,
        "paymentStatus": "Paid",
        "deliveryStatus": "Pending",
        "expires": "2026-08-15T00:00:00",
        "quantity": 1,
    },
]


def _configured() -> bool:
    return bool(EMAIL and PASSWORD)


async def _login(client: httpx.AsyncClient) -> str:
    r = await client.post(
        f"{API_URL}/Auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["token"]


async def _fetch_purchases() -> list[dict]:
    """Fetch the user's purchases, logging in (and re-logging in on 401) as needed."""
    global _TOKEN
    async with httpx.AsyncClient() as client:
        if _TOKEN is None:
            _TOKEN = await _login(client)
        r = await client.get(
            f"{API_URL}/Purchases",
            headers={"Authorization": f"Bearer {_TOKEN}"},
            timeout=20,
        )
        if r.status_code == 401:
            _TOKEN = await _login(client)
            r = await client.get(
                f"{API_URL}/Purchases",
                headers={"Authorization": f"Bearer {_TOKEN}"},
                timeout=20,
            )
        r.raise_for_status()
        return r.json()


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def summarize(purchases: list[dict]) -> dict:
    """Roll purchases up into the dashboard numbers PowerBuy tracks."""
    now = datetime.now(timezone.utc)
    unpaid = not_delivered = expiring_soon = 0
    expected_profit = 0.0
    for p in purchases:
        if str(p.get("paymentStatus", "")).lower() != "paid":
            unpaid += 1
        if str(p.get("deliveryStatus", "")).lower() != "delivered":
            not_delivered += 1
        exp = _parse_dt(p.get("expires"))
        if exp is not None:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            days = (exp - now).days
            if 0 <= days <= 7:
                expiring_soon += 1
        expected_profit += float(p.get("profit7Percent") or 0)
    return {
        "total_purchases": len(purchases),
        "expected_profit": round(expected_profit, 2),
        "unpaid_count": unpaid,
        "not_delivered_count": not_delivered,
        "expiring_soon_count": expiring_soon,
    }


@app.get("/health")
async def health():
    return {"service": "powerbuy", "mode": "live" if _configured() else "mock"}


@app.get("/purchases")
async def purchases():
    if not _configured():
        return {"purchases": MOCK_PURCHASES, "mode": "mock"}
    try:
        data = await _fetch_purchases()
        return {"purchases": data, "mode": "live"}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"error": "powerbuy upstream failed", "detail": str(exc)}, status_code=502
        )


@app.get("/summary")
async def summary():
    """Dashboard numbers: expected profit, unpaid, not delivered, expiring soon."""
    if not _configured():
        return {"summary": summarize(MOCK_PURCHASES), "mode": "mock"}
    try:
        data = await _fetch_purchases()
        return {"summary": summarize(data), "mode": "live"}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"error": "powerbuy upstream failed", "detail": str(exc)}, status_code=502
        )


@app.get("/")
async def root():
    return {"app": "PowerBuy", "endpoints": ["/purchases", "/summary", "/health"]}
