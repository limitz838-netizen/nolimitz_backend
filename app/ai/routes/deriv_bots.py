"""
NolimitzBots — Deriv Bots Engine  [v1]
======================================
Server-side automated trading bots ("Dbots") for connected Deriv accounts.
Bots run as asyncio tasks inside the backend — they keep trading even when
the user closes the app. One bot per user in v1.

Endpoints:
  GET  /bots/strategies                    -> list of available strategies
  POST /bots/start   {user_id, account_id, strategy, config}
  POST /bots/stop    {user_id}
  GET  /bots/status?user_id=X              -> status, session stats, trades

Strategy v1 registry:
  momentum   — trades in the direction of the last N ticks (Rise/Fall)
  reversal   — trades against the last N ticks (Rise/Fall)

Safety (enforced server-side, all configurable per run):
  max_trades           hard cap on trades per session       (default 10)
  session_take_profit  stop when session profit >= this     (default 5.0)
  session_stop_loss    stop when session loss  >= this      (default 5.0)
  cooldown_s           pause between trades                 (default 10)
  stake                fixed stake per trade                (default 1.0)

Wire-up in main.py:
  from app.ai.routes.deriv_bots import router as deriv_bots_router, init_bot_tables
  init_bot_tables()
  app.include_router(deriv_bots_router)

NOTE: bots stop when the server restarts (e.g. on deploy). Status is set to
'stopped' with reason 'server_restart' on startup so the UI reflects reality.
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

# In-process registry of running bot tasks: {user_id: asyncio.Task}
_RUNNING: dict[str, asyncio.Task] = {}

STRATEGIES = [
    {
        "id": "momentum",
        "name": "Nolimitz Momentum",
        "description": "Follows the trend — trades in the direction of the "
                       "last few ticks. Best on Volatility indices.",
        "risk": "medium",
    },
    {
        "id": "reversal",
        "name": "Nolimitz Reversal",
        "description": "Fades the move — trades against the last few ticks, "
                       "betting on a snap-back.",
        "risk": "medium",
    },
]

DEFAULT_CONFIG = {
    "symbol": "R_100",
    "stake": 1.0,
    "duration": 5,            # ticks
    "ticks_window": 3,        # how many ticks the strategy looks at
    "max_trades": 10,
    "session_take_profit": 5.0,
    "session_stop_loss": 5.0,
    "cooldown_s": 10,
    "currency": "USD",
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
        # Server just (re)started: anything marked running is no longer real
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
    """Collect `count` live ticks from the public stream."""
    prices: list[float] = []
    async with websockets.connect(WS_PUBLIC, open_timeout=WS_TIMEOUT) as ws:
        await ws.send(json.dumps({"ticks": symbol, "subscribe": 1, "req_id": 1}))
        deadline = time.time() + 60
        while len(prices) < count and time.time() < deadline:
            msg = json.loads(await ws.recv())
            if msg.get("error"):
                raise RuntimeError(msg["error"].get("message", "tick error"))
            tick = msg.get("tick")
            if tick and tick.get("quote") is not None:
                prices.append(float(tick["quote"]))
    return prices


async def _bot_buy(token_ws_url: str, contract_type: str, cfg: dict) -> dict:
    """Proposal + buy on one authenticated WS session."""
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
    async with websockets.connect(token_ws_url, open_timeout=WS_TIMEOUT) as ws:
        await ws.send(json.dumps(proposal))
        prop = None
        deadline = time.time() + WS_TIMEOUT
        while time.time() < deadline:
            msg = json.loads(await ws.recv())
            if msg.get("error"):
                raise RuntimeError(msg["error"].get("message", "proposal error"))
            if "proposal" in msg:
                prop = msg["proposal"]
                break
        if prop is None:
            raise RuntimeError("no proposal")

        await ws.send(json.dumps(
            {"buy": prop["id"], "price": prop["ask_price"], "req_id": 2}))
        deadline = time.time() + WS_TIMEOUT
        while time.time() < deadline:
            msg = json.loads(await ws.recv())
            if msg.get("error"):
                raise RuntimeError(msg["error"].get("message", "buy error"))
            if "buy" in msg:
                return msg["buy"]
    raise RuntimeError("buy timeout")


async def _contract_profit(user_id: str, account_id: str,
                           contract_id: int) -> float | None:
    """Look up the finished contract's profit from the profit table."""
    token = get_deriv_token(user_id)
    if token is None:
        return None
    ws_url = await _get_ws_url(token, account_id)
    async with websockets.connect(ws_url, open_timeout=WS_TIMEOUT) as ws:
        await ws.send(json.dumps(
            {"profit_table": 1, "limit": 25, "sort": "DESC",
             "description": 1, "req_id": 1}))
        deadline = time.time() + WS_TIMEOUT
        while time.time() < deadline:
            msg = json.loads(await ws.recv())
            if msg.get("error"):
                return None
            table = msg.get("profit_table")
            if table:
                for tx in table.get("transactions", []):
                    if tx.get("contract_id") == contract_id:
                        try:
                            sell = float(tx.get("sell_price", 0))
                            buy = float(tx.get("buy_price", 0))
                            return round(sell - buy, 2)
                        except (TypeError, ValueError):
                            return None
                return None
    return None


# -------------------------------------------------------------- BOT LOOP ---
def _decide(strategy: str, prices: list[float]) -> str | None:
    """Return CALL / PUT / None based on the tick window."""
    if len(prices) < 2:
        return None
    ups = sum(1 for a, b in zip(prices, prices[1:]) if b > a)
    downs = sum(1 for a, b in zip(prices, prices[1:]) if b < a)
    if ups == downs:
        return None  # no clear direction — skip this round
    trend = "CALL" if ups > downs else "PUT"
    if strategy == "momentum":
        return trend
    if strategy == "reversal":
        return "PUT" if trend == "CALL" else "CALL"
    return None


async def _run_bot(user_id: str, account_id: str, strategy: str, cfg: dict):
    trades_done = 0
    session_pnl = 0.0
    stop_reason = "finished"

    try:
        while trades_done < cfg["max_trades"]:
            # Safety gates
            if session_pnl >= cfg["session_take_profit"]:
                stop_reason = "take_profit_hit"
                break
            if session_pnl <= -cfg["session_stop_loss"]:
                stop_reason = "stop_loss_hit"
                break

            token = get_deriv_token(user_id)
            if token is None:
                stop_reason = "deriv_disconnected"
                break

            # 1) read the market
            prices = await _get_ticks(cfg["symbol"], cfg["ticks_window"] + 1)
            direction = _decide(strategy, prices)
            if direction is None:
                await asyncio.sleep(3)
                continue

            # 2) trade
            ws_url = await _get_ws_url(token, account_id)
            buy = await _bot_buy(ws_url, direction, cfg)
            contract_id = buy.get("contract_id")
            longcode = buy.get("longcode", "")
            trades_done += 1

            # 3) wait for the contract to finish, then record the result
            await asyncio.sleep(cfg["duration"] * 2 + 6)
            profit = await _contract_profit(user_id, account_id, contract_id)
            if profit is not None:
                session_pnl = round(session_pnl + profit, 2)
            _log_trade(user_id, contract_id, direction, cfg["stake"],
                       profit if profit is not None else 0.0, longcode)
            _update_bot(user_id, trades_done=trades_done,
                        session_pnl=session_pnl)

            # 4) cooldown
            await asyncio.sleep(cfg["cooldown_s"])

    except asyncio.CancelledError:
        stop_reason = "user_stopped"
        raise
    except Exception as exc:  # noqa: BLE001 — record and stop cleanly
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
    # hard server-side sanity caps
    cfg["stake"] = max(0.35, min(float(cfg["stake"]), 100.0))
    cfg["max_trades"] = max(1, min(int(cfg["max_trades"]), 50))
    cfg["cooldown_s"] = max(3, int(cfg["cooldown_s"]))

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

    # reconcile: DB says running but no live task (shouldn't happen, but safe)
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