"""
NolimitzBots — Deriv Trading Proxy Router  [v1]
===============================================
Server-side bridge between the NolimitzBots frontend and Deriv's trading API.
The browser only ever talks to THIS server; user tokens never leave the backend.

How Deriv's new API works:
  - REST  (Bearer token)  -> list accounts, request an OTP
  - The OTP response contains an authenticated WebSocket URL
  - All trading (proposal/buy/sell/portfolio/balance/ticks) happens over WS

This router opens a short-lived WS session per request — simple and reliable
for manual trading. (The AI worker will hold persistent sessions later.)

Endpoints (all keyed on user_id, same as deriv_oauth):
  GET  /deriv/accounts?user_id=X
  GET  /deriv/balance?user_id=X&account_id=CR123
  GET  /deriv/symbols                      (public — no auth)
  GET  /deriv/price?symbol=R_100           (public — latest tick)
  POST /deriv/buy        {user_id, account_id, contract}   -> proposal + buy
  GET  /deriv/positions?user_id=X&account_id=CR123
  POST /deriv/sell       {user_id, account_id, contract_id, price?}

Contract payload examples (body.contract):
  Rise/Fall:   {"type":"CALL","symbol":"R_100","stake":10,"currency":"USD",
                "duration":5,"duration_unit":"t"}
               ("CALL" = Rise, "PUT" = Fall; duration_unit: t/s/m/h/d)
  Multipliers: {"type":"MULTUP","symbol":"R_100","stake":10,"currency":"USD",
                "multiplier":100,"stop_loss":5,"take_profit":15}
               ("MULTUP" = up, "MULTDOWN" = down; SL/TP in currency amount)

Wire-up in main.py:
  from app.ai.routes.deriv_trading import router as deriv_trading_router
  app.include_router(deriv_trading_router)

Requires:  pip install websockets   (add `websockets` to requirements.txt)
"""

import json
import time

import httpx
import websockets
from fastapi import APIRouter, Body, HTTPException, Query

from app.ai.routes.deriv_oauth import get_deriv_token

# ---------------------------------------------------------------- CONFIG ---
REST_BASE = "https://api.derivws.com/trading/v1/options"
WS_PUBLIC = "wss://api.derivws.com/trading/v1/options/ws/public"
WS_TIMEOUT = 25  # seconds to wait for a WS reply

router = APIRouter(prefix="/deriv", tags=["deriv-trading"])


# --------------------------------------------------------------- HELPERS ---
def _token_or_401(user_id: str) -> str:
    token = get_deriv_token(user_id)
    if token is None:
        raise HTTPException(
            401,
            "Deriv session missing or expired — reconnect your Deriv account.",
        )
    return token


async def _get_ws_url(token: str, account_id: str) -> str:
    """Ask Deriv for an OTP; the response contains a ready authenticated WS URL."""
    async with httpx.AsyncClient(timeout=20) as http:
        resp = await http.post(
            f"{REST_BASE}/accounts/{account_id}/otp",
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        raise HTTPException(
            502, f"Deriv OTP request failed ({resp.status_code}): {resp.text[:200]}"
        )
    data = resp.json()
    # Tolerant parsing — Deriv returns a ready-to-use WS URL in the OTP response
    for key in ("websocket_url", "ws_url", "url"):
        if isinstance(data.get(key), str):
            return data[key]
    # Fallback: build it from the raw OTP if only the code is returned
    otp = data.get("otp") or data.get("token")
    if otp:
        account_type = "demo" if account_id.upper().startswith("VR") else "real"
        return f"wss://api.derivws.com/trading/v1/options/ws/{account_type}?otp={otp}"
    raise HTTPException(502, f"Unexpected OTP response shape: {str(data)[:200]}")


async def _ws_call(ws_url: str, request: dict, expect_key: str) -> dict:
    """Open a WS, send one request, wait for the reply that carries expect_key."""
    async with websockets.connect(ws_url, open_timeout=WS_TIMEOUT) as ws:
        await ws.send(json.dumps(request))
        deadline = time.time() + WS_TIMEOUT
        while time.time() < deadline:
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("error"):
                raise HTTPException(
                    400, msg["error"].get("message", "Deriv rejected the request")
                )
            if expect_key in msg or msg.get("msg_type") == expect_key:
                return msg
    raise HTTPException(504, "Deriv did not reply in time")


async def _ws_call_auth(
    user_id: str, account_id: str, request: dict, expect_key: str
) -> dict:
    token = _token_or_401(user_id)
    ws_url = await _get_ws_url(token, account_id)
    return await _ws_call(ws_url, request, expect_key)


# ------------------------------------------------------------- ENDPOINTS ---
@router.get("/accounts")
async def deriv_accounts(user_id: str = Query(...)):
    """List the user's Deriv accounts (demo + real) via REST."""
    token = _token_or_401(user_id)
    async with httpx.AsyncClient(timeout=20) as http:
        resp = await http.get(
            f"{REST_BASE}/accounts",
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        raise HTTPException(502, f"Deriv accounts failed ({resp.status_code})")
    return resp.json()


@router.get("/balance")
async def deriv_balance(user_id: str = Query(...), account_id: str = Query(...)):
    msg = await _ws_call_auth(
        user_id, account_id, {"balance": 1, "req_id": 1}, "balance"
    )
    return msg.get("balance", msg)


@router.get("/symbols")
async def deriv_symbols():
    """Public — list tradeable symbols (Volatility, Boom/Crash, forex...)."""
    msg = await _ws_call(
        WS_PUBLIC,
        {"active_symbols": "brief", "req_id": 1},
        "active_symbols",
    )
    return {"symbols": msg.get("active_symbols", [])}


@router.get("/price")
async def deriv_price(symbol: str = Query(..., min_length=2)):
    """Public — latest tick for a symbol (e.g. R_100, BOOM500, frxEURUSD)."""
    msg = await _ws_call(WS_PUBLIC, {"ticks": symbol, "req_id": 1}, "tick")
    tick = msg.get("tick", {})
    return {
        "symbol": symbol,
        "price": tick.get("quote"),
        "epoch": tick.get("epoch"),
    }


@router.post("/buy")
async def deriv_buy(
    user_id: str = Body(...),
    account_id: str = Body(...),
    contract: dict = Body(...),
):
    """
    Two-step on one WS session: proposal -> buy.
    Supports Rise/Fall (CALL/PUT) and Multipliers (MULTUP/MULTDOWN).
    """
    ctype = str(contract.get("type", "")).upper()
    symbol = contract.get("symbol")
    stake = contract.get("stake")
    currency = contract.get("currency", "USD")
    if not (ctype and symbol and stake):
        raise HTTPException(422, "contract needs at least: type, symbol, stake")

    proposal: dict = {
        "proposal": 1,
        "contract_type": ctype,
        "symbol": symbol,
        "amount": stake,
        "basis": "stake",
        "currency": currency,
        "req_id": 1,
    }

    if ctype in ("MULTUP", "MULTDOWN"):
        if not contract.get("multiplier"):
            raise HTTPException(422, "multiplier contracts need: multiplier")
        proposal["multiplier"] = contract["multiplier"]
        limit_order = {}
        if contract.get("stop_loss"):
            limit_order["stop_loss"] = contract["stop_loss"]
        if contract.get("take_profit"):
            limit_order["take_profit"] = contract["take_profit"]
        if limit_order:
            proposal["limit_order"] = limit_order
    else:
        # Options (Rise/Fall, Touch, Digits, ...) need a duration
        if not contract.get("duration"):
            raise HTTPException(422, "option contracts need: duration, duration_unit")
        proposal["duration"] = contract["duration"]
        proposal["duration_unit"] = contract.get("duration_unit", "t")
        if contract.get("barrier") is not None:
            proposal["barrier"] = contract["barrier"]

    token = _token_or_401(user_id)
    ws_url = await _get_ws_url(token, account_id)

    async with websockets.connect(ws_url, open_timeout=WS_TIMEOUT) as ws:
        # Step 1 — proposal
        await ws.send(json.dumps(proposal))
        prop = None
        deadline = time.time() + WS_TIMEOUT
        while time.time() < deadline:
            msg = json.loads(await ws.recv())
            if msg.get("error"):
                raise HTTPException(400, msg["error"].get("message", "proposal error"))
            if "proposal" in msg:
                prop = msg["proposal"]
                break
        if prop is None:
            raise HTTPException(504, "No proposal received from Deriv")

        # Step 2 — buy at the proposed price
        await ws.send(
            json.dumps(
                {"buy": prop["id"], "price": prop["ask_price"], "req_id": 2}
            )
        )
        deadline = time.time() + WS_TIMEOUT
        while time.time() < deadline:
            msg = json.loads(await ws.recv())
            if msg.get("error"):
                raise HTTPException(400, msg["error"].get("message", "buy error"))
            if "buy" in msg:
                return {
                    "ok": True,
                    "contract_id": msg["buy"].get("contract_id"),
                    "buy_price": msg["buy"].get("buy_price"),
                    "payout": msg["buy"].get("payout"),
                    "longcode": msg["buy"].get("longcode"),
                    "transaction_id": msg["buy"].get("transaction_id"),
                }
    raise HTTPException(504, "Buy confirmation not received in time")


@router.get("/positions")
async def deriv_positions(user_id: str = Query(...), account_id: str = Query(...)):
    """Open contracts for the account."""
    msg = await _ws_call_auth(
        user_id, account_id, {"portfolio": 1, "req_id": 1}, "portfolio"
    )
    return msg.get("portfolio", msg)


@router.post("/sell")
async def deriv_sell(
    user_id: str = Body(...),
    account_id: str = Body(...),
    contract_id: int = Body(...),
    price: float = Body(0),  # 0 = sell at market
):
    msg = await _ws_call_auth(
        user_id,
        account_id,
        {"sell": contract_id, "price": price, "req_id": 1},
        "sell",
    )
    return {"ok": True, "sell": msg.get("sell", msg)}