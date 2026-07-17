"""
NolimitzBots — Deriv Bots Engine  [v2 — discipline + Spike Hunter]
==================================================================
v2 changes (safety-first upgrade):
  1. STRICTER ENTRIES: momentum/reversal now require a CLEAN streak — every
     tick in the window moving the same way. Mixed ticks = skip, no trade.
  2. LOSS-STREAK PROTECTION: after `max_consec_losses` losing trades in a row
     (default 3) the bot stops itself — stop_reason 'loss_streak_protection'.
  3. NEW STRATEGY — Spike Hunter (Boom/Crash only): rides the spike direction
     with a MULTIPLIER contract and hard per-trade SL/TP derived from stake
     (TP = +100% of stake, SL = -50% of stake). If a contract is still open
     after `mult_timeout_s`, it is force-closed at market.
  4. NO MARTINGALE anywhere. Fixed stake always.

Endpoints (unchanged):
  GET  /bots/strategies
  POST /bots/start   {user_id, account_id, strategy, config}
  POST /bots/stop    {user_id}
  GET  /bots/status?user_id=X

Wire-up in main.py (unchanged):
  from app.ai.routes.deriv_bots import router as deriv_bots_router, init_bot_tables
  init_bot_tables()
  app.include_router(deriv_bots_router)
"""

import asyncio
import json
import time

import websockets
from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import text

from app.database import engine
from app.ai.routes.deriv_oauth import get_deriv_token
from app.ai.routes.deriv_trading import WS_PUBLIC, WS_TIMEOUT, _get_ws_url

router = APIRouter(prefix="/bots", tags=["deriv-bots"])

_RUNNING: dict[str, asyncio.Task] = {}

STRATEGIES = [
    {
        "id": "momentum",
        "name": "Nolimitz Momentum",
        "description": "Trades ONLY on a clean streak — every tick moving the "
                       "same way. Mixed markets are skipped. Best on "
                       "Volatility indices.",
        "risk": "medium",
    },
    {
        "id": "reversal",
        "name": "Nolimitz Reversal",
        "description": "Waits for a clean one-way streak, then fades it — "
                       "betting on the snap-back. Skips choppy markets.",
        "risk": "medium",
    },
    {
        "id": "spike",
        "name": "Spike Hunter",
        "description": "Boom & Crash specialist. Rides the spike direction "
                       "with a multiplier and a hard stop: risk 50% of stake "
                       "to win 100%. Force-closes stuck trades.",
        "risk": "high",
    },
]

DEFAULT_CONFIG = {
    "symbol": "R_100",
    "stake": 1.0,
    "duration": 5,             # ticks (options strategies)
    "ticks_window": 3,         # streak length required
    "max_trades": 10,
    "max_consec_losses": 3,    # loss-streak circuit breaker
    "session_take_profit": 5.0,
    "session_stop_loss": 5.0,
    "cooldown_s": 10,
    "currency": "USD",
    # Spike Hunter only:
    "multiplier": 100,
    "mult_timeout_s": 180,     # force-close if still open after this
}


# ------------------------------------------------------------------- DB ---
def init_bot_tables():
    with engine.begin() as conn:
        conn.execute(
            text(
                """CREATE TABLE IF NOT EXISTS deriv_bots (
                       user_id      VARCHAR(128) PRIMARY KEY,
                       account_id   VARCHAR(64)  NOT NULL,
                       strategy     VARCHAR(64)  NOT NULL,
                       config_json  TEXT NOT NULL,
                       status       VARCHAR(24)  NOT NULL,
                       stop_reason  VARCHAR(64),
                       trades_done  INTEGER DEFAULT 0,
                       session_pnl  DOUBLE PRECISION DEFAULT 0,
                       started_at   BIGINT,
                       updated_at   BIGINT
                   )"""
            )
        )
        conn.execute(
            text(
                """CREATE TABLE IF NOT EXISTS deriv_bot_trades (
                       id           SERIAL PRIMARY KEY,
                       user_id      VARCHAR(128) NOT NULL,
                       contract_id  BIGINT,
                       direction    VARCHAR(8),
                       stake        DOUBLE PRECISION,
                       profit       DOUBLE PRECISION,
                       longcode     TEXT,
                       created_at   BIGINT
                   )"""
            )
        )
        conn.execute(
            text(
                "UPDATE deriv_bots SET status='stopped', "
                "stop_reason='server_restart', updated_at=:t "
                "WHERE status='running'"
            ),
            {"t": int(time.time())},
        )


def _save_bot(user_id, account_id, strategy, config, status,
              reason=None, trades=0, pnl=0.0):
    now = int(time.time())
    with engine.begin() as conn:
        conn.execute(
            text(
                """INSERT INTO deriv_bots
                       (user_id, account_id, strategy, config_json, status,
                        stop_reason, trades_done, session_pnl, started_at,
                        updated_at)
                   VALUES (:u,:a,:s,:c,:st,:r,:td,:p,:t,:t)
                   ON CONFLICT (user_id) DO UPDATE SET
                       account_id=:a, strategy=:s, config_json=:c, status=:st,
                       stop_reason=:r, trades_done=:td, session_pnl=:p,
                       started_at=CASE WHEN :st='running'
                                       THEN :t ELSE deriv_bots.started_at END,
                       updated_at=:t"""
            ),
            {"u": user_id, "a": account_id, "s": strategy,
             "c": json.dumps(config), "st": status, "r": reason,
             "td": trades, "p": pnl, "t": now},
        )


def _update_bot(user_id, **fields):
    fields["updated_at"] = int(time.time())
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    with engine.begin() as conn:
        conn.execute(
            text(f"UPDATE deriv_bots SET {sets} WHERE user_id = :user_id"),
            {**fields, "user_id": user_id},
        )


def _log_trade(user_id, contract_id, direction, stake, profit, longcode):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO deriv_bot_trades "
                "(user_id, contract_id, direction, stake, profit, longcode, "
                "created_at) VALUES (:u,:c,:d,:s,:p,:l,:t)"
            ),
            {"u": user_id, "c": contract_id, "d": direction, "s": stake,
             "p": profit, "l": longcode, "t": int(time.time())},
        )


# --------------------------------------------------------- DERIV HELPERS ---
async def _get_ticks(symbol: str, count: int) -> list[float]:
    prices: list[float] = []
    async with websockets.connect(WS_PUBLIC, open_timeout=WS_TIMEOUT) as ws:
        await ws.send(json.dumps({"ticks": symbol, "subscribe": 1, "req_id": 1}))
        deadline = time.time() + 90
        while len(prices) < count and time.time() < deadline:
            msg = json.loads(await ws.recv())
            if msg.get("error"):
                raise RuntimeError(msg["error"].get("message", "tick error"))
            tick = msg.get("tick")
            if tick and tick.get("quote") is not None:
                prices.append(float(tick["quote"]))
    return prices


async def _ws_request(ws, payload: dict, expect_key: str) -> dict:
    await ws.send(json.dumps(payload))
    deadline = time.time() + WS_TIMEOUT
    while time.time() < deadline:
        msg = json.loads(await ws.recv())
        if msg.get("error"):
            raise RuntimeError(msg["error"].get("message", "deriv error"))
        if expect_key in msg:
            return msg
    raise RuntimeError(f"timeout waiting for {expect_key}")


async def _bot_buy_option(ws_url: str, contract_type: str, cfg: dict) -> dict:
    """Rise/Fall option: proposal + buy."""
    proposal = {
        "proposal": 1,
        "contract_type": contract_type,
        "underlying_symbol": cfg["symbol"],
        "amount": cfg["stake"],
        "basis": "stake",
        "currency": cfg.get("currency", "USD"),
        "duration": cfg["duration"],
        "duration_unit": "t",
        "req_id": 1,
    }
    async with websockets.connect(ws_url, open_timeout=WS_TIMEOUT) as ws:
        prop = (await _ws_request(ws, proposal, "proposal"))["proposal"]
        buy = (await _ws_request(
            ws, {"buy": prop["id"], "price": prop["ask_price"], "req_id": 2},
            "buy"))["buy"]
        return buy


async def _bot_buy_multiplier(ws_url: str, contract_type: str,
                              cfg: dict) -> dict:
    """MULTUP/MULTDOWN with hard per-trade SL/TP derived from stake."""
    stake = float(cfg["stake"])
    proposal = {
        "proposal": 1,
        "contract_type": contract_type,
        "underlying_symbol": cfg["symbol"],
        "amount": stake,
        "basis": "stake",
        "currency": cfg.get("currency", "USD"),
        "multiplier": int(cfg.get("multiplier", 100)),
        "limit_order": {
            "take_profit": round(stake * 1.0, 2),   # win 100% of stake
            "stop_loss": round(stake * 0.5, 2),     # risk 50% of stake
        },
        "req_id": 1,
    }
    async with websockets.connect(ws_url, open_timeout=WS_TIMEOUT) as ws:
        prop = (await _ws_request(ws, proposal, "proposal"))["proposal"]
        buy = (await _ws_request(
            ws, {"buy": prop["id"], "price": prop["ask_price"], "req_id": 2},
            "buy"))["buy"]
        return buy


async def _force_sell(ws_url: str, contract_id: int):
    try:
        async with websockets.connect(ws_url, open_timeout=WS_TIMEOUT) as ws:
            await _ws_request(
                ws, {"sell": contract_id, "price": 0, "req_id": 1}, "sell")
    except Exception:
        pass  # if it already closed, that's fine


async def _contract_profit(user_id: str, account_id: str,
                           contract_id: int) -> float | None:
    token = get_deriv_token(user_id)
    if token is None:
        return None
    ws_url = await _get_ws_url(token, account_id)
    try:
        async with websockets.connect(ws_url, open_timeout=WS_TIMEOUT) as ws:
            msg = await _ws_request(
                ws,
                {"profit_table": 1, "limit": 25, "sort": "DESC",
                 "description": 1, "req_id": 1},
                "profit_table")
            for tx in msg["profit_table"].get("transactions", []):
                if tx.get("contract_id") == contract_id:
                    try:
                        return round(float(tx.get("sell_price", 0)) -
                                     float(tx.get("buy_price", 0)), 2)
                    except (TypeError, ValueError):
                        return None
    except Exception:
        return None
    return None


# --------------------------------------------------------------- SIGNALS ---
def _clean_streak(prices: list[float]) -> str | None:
    """Return 'up'/'down' only if EVERY move in the window agrees."""
    if len(prices) < 2:
        return None
    moves = [b - a for a, b in zip(prices, prices[1:])]
    if all(m > 0 for m in moves):
        return "up"
    if all(m < 0 for m in moves):
        return "down"
    return None


def _decide(strategy: str, prices: list[float], symbol: str) -> str | None:
    streak = _clean_streak(prices)
    if strategy == "momentum":
        if streak == "up":
            return "CALL"
        if streak == "down":
            return "PUT"
        return None
    if strategy == "reversal":
        if streak == "up":
            return "PUT"
        if streak == "down":
            return "CALL"
        return None
    if strategy == "spike":
        # Boom spikes UP, Crash spikes DOWN — ride the spike direction.
        sym = symbol.upper()
        if "BOOM" in sym:
            return "MULTUP"
        if "CRASH" in sym:
            return "MULTDOWN"
        return None
    return None


# -------------------------------------------------------------- BOT LOOP ---
async def _run_bot(user_id: str, account_id: str, strategy: str, cfg: dict):
    trades_done = 0
    session_pnl = 0.0
    consec_losses = 0
    stop_reason = "finished"

    try:
        while trades_done < cfg["max_trades"]:
            if session_pnl >= cfg["session_take_profit"]:
                stop_reason = "take_profit_hit"
                break
            if session_pnl <= -cfg["session_stop_loss"]:
                stop_reason = "stop_loss_hit"
                break
            if consec_losses >= cfg.get("max_consec_losses", 3):
                stop_reason = "loss_streak_protection"
                break

            token = get_deriv_token(user_id)
            if token is None:
                stop_reason = "deriv_disconnected"
                break

            # 1) read the market — only trade clean setups
            prices = await _get_ticks(cfg["symbol"], cfg["ticks_window"] + 1)
            direction = _decide(strategy, prices, cfg["symbol"])
            if direction is None:
                await asyncio.sleep(3)
                continue

            # 2) trade
            ws_url = await _get_ws_url(token, account_id)
            if direction in ("MULTUP", "MULTDOWN"):
                buy = await _bot_buy_multiplier(ws_url, direction, cfg)
            else:
                buy = await _bot_buy_option(ws_url, direction, cfg)
            contract_id = buy.get("contract_id")
            longcode = buy.get("longcode", "")
            trades_done += 1

            # 3) wait for the contract to finish, then record the result
            if direction in ("MULTUP", "MULTDOWN"):
                profit = None
                waited = 0
                timeout = int(cfg.get("mult_timeout_s", 180))
                while waited < timeout:
                    await asyncio.sleep(10)
                    waited += 10
                    profit = await _contract_profit(
                        user_id, account_id, contract_id)
                    if profit is not None:
                        break
                if profit is None:
                    # still open past timeout — force close, then re-check
                    ws_url2 = await _get_ws_url(
                        get_deriv_token(user_id) or token, account_id)
                    await _force_sell(ws_url2, contract_id)
                    await asyncio.sleep(6)
                    profit = await _contract_profit(
                        user_id, account_id, contract_id)
            else:
                await asyncio.sleep(cfg["duration"] * 2 + 6)
                profit = await _contract_profit(
                    user_id, account_id, contract_id)

            if profit is not None:
                session_pnl = round(session_pnl + profit, 2)
                consec_losses = consec_losses + 1 if profit < 0 else 0
            _log_trade(user_id, contract_id, direction, cfg["stake"],
                       profit if profit is not None else 0.0, longcode)
            _update_bot(user_id, trades_done=trades_done,
                        session_pnl=session_pnl)

            # 4) cooldown (longer after a loss — cheap discipline)
            extra = cfg["cooldown_s"] if (profit or 0) < 0 else 0
            await asyncio.sleep(cfg["cooldown_s"] + extra)

    except asyncio.CancelledError:
        stop_reason = "user_stopped"
        raise
    except Exception as exc:  # noqa: BLE001
        stop_reason = f"error:{str(exc)[:40]}"
    finally:
        _RUNNING.pop(user_id, None)
        _update_bot(user_id, status="stopped", stop_reason=stop_reason,
                    trades_done=trades_done, session_pnl=session_pnl)


# ------------------------------------------------------------- ENDPOINTS ---
@router.get("/strategies")
def bot_strategies():
    return {"strategies": STRATEGIES, "default_config": DEFAULT_CONFIG}


@router.post("/start")
async def bot_start(
    user_id: str = Body(...),
    account_id: str = Body(...),
    strategy: str = Body(...),
    config: dict = Body(default={}),
):
    if strategy not in {s["id"] for s in STRATEGIES}:
        raise HTTPException(422, "Unknown strategy")
    if user_id in _RUNNING and not _RUNNING[user_id].done():
        raise HTTPException(409, "A bot is already running for this user")
    if get_deriv_token(user_id) is None:
        raise HTTPException(401, "Deriv not connected — connect first")

    cfg = {**DEFAULT_CONFIG, **(config or {})}
    cfg["stake"] = max(0.35, min(float(cfg["stake"]), 100.0))
    cfg["max_trades"] = max(1, min(int(cfg["max_trades"]), 50))
    cfg["cooldown_s"] = max(3, int(cfg["cooldown_s"]))
    cfg["max_consec_losses"] = max(1, min(
        int(cfg.get("max_consec_losses", 3)), 10))

    sym = str(cfg["symbol"]).upper()
    if strategy == "spike" and ("BOOM" not in sym and "CRASH" not in sym):
        raise HTTPException(
            422, "Spike Hunter only works on Boom or Crash indices — "
                 "pick a Boom/Crash market.")

    _save_bot(user_id, account_id, strategy, cfg, "running")
    task = asyncio.create_task(_run_bot(user_id, account_id, strategy, cfg))
    _RUNNING[user_id] = task
    return {"ok": True, "status": "running", "config": cfg}


@router.post("/stop")
async def bot_stop(user_id: str = Body(..., embed=True)):
    task = _RUNNING.get(user_id)
    if task and not task.done():
        task.cancel()
    else:
        _update_bot(user_id, status="stopped", stop_reason="user_stopped")
    return {"ok": True, "status": "stopping"}


@router.get("/status")
def bot_status(user_id: str = Query(...)):
    with engine.connect() as conn:
        bot = conn.execute(
            text("SELECT account_id, strategy, config_json, status, "
                 "stop_reason, trades_done, session_pnl, started_at "
                 "FROM deriv_bots WHERE user_id = :u"),
            {"u": user_id},
        ).fetchone()
        trades = conn.execute(
            text("SELECT contract_id, direction, stake, profit, longcode, "
                 "created_at FROM deriv_bot_trades WHERE user_id = :u "
                 "ORDER BY id DESC LIMIT 20"),
            {"u": user_id},
        ).fetchall()

    if bot is None:
        return {"exists": False, "status": "none"}

    status = bot[3]
    if status == "running" and (
        user_id not in _RUNNING or _RUNNING[user_id].done()
    ):
        status = "stopped"

    return {
        "exists": True,
        "status": status,
        "account_id": bot[0],
        "strategy": bot[1],
        "config": json.loads(bot[2]),
        "stop_reason": bot[4],
        "trades_done": bot[5],
        "session_pnl": bot[6],
        "started_at": bot[7],
        "trades": [
            {"contract_id": t[0], "direction": t[1], "stake": t[2],
             "profit": t[3], "longcode": t[4], "time": t[5]}
            for t in trades
        ],
    }