import os

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="PowerBuy Service")

UPSTREAM = os.environ.get("POWERBUY_UPSTREAM_URL", "").rstrip("/")
API_KEY = os.environ.get("POWERBUY_API_KEY", "")

MOCK_DEALS = [
    {"id": "d1", "item": "DeWalt 20V Drill", "price": 89.0, "was": 149.0, "signal": "buy"},
    {"id": "d2", "item": "Whey Isolate 5lb", "price": 42.0, "was": 59.0, "signal": "buy"},
    {"id": "d3", "item": "Mechanical Keyboard", "price": 110.0, "was": 120.0, "signal": "watch"},
]


@app.get("/health")
async def health():
    return {"service": "powerbuy", "mode": "upstream" if UPSTREAM else "mock"}


async def _upstream_get(path: str):
    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{UPSTREAM}{path}", headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()


@app.get("/deals")
async def deals():
    """Current deals / buy signals. Proxies your real PowerBuy API when configured."""
    if UPSTREAM:
        try:
            return await _upstream_get("/deals")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"error": "powerbuy upstream failed", "detail": str(exc)}, status_code=502
            )
    return {"deals": MOCK_DEALS, "mode": "mock"}


@app.get("/")
async def root():
    return {"app": "PowerBuy", "endpoints": ["/deals", "/health"]}
