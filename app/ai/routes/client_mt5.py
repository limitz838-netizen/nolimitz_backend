"""
================================================================================
  CLIENT MT5 + AI API ENDPOINTS
================================================================================

  FLOW (matches Lovable frontend's polling expectation):
    1. POST /api/client/mt5-account
         - Saves credentials immediately
         - Sets verification_status = "VERIFYING"
         - Spawns a background thread to verify
         - Returns within ~50ms (no hanging)
    2. Frontend polls GET /api/client/ai/mt5-status every 3 seconds
    3. Background thread completes verification (~1-5 seconds typically):
         - Success: sets verification_status = "VERIFIED" + saves balance etc
         - Failure: sets verification_status = "FAILED" + saves error_reason
    4. Frontend sees verified=true on next poll → shows balance, broker, name
    5. If after 20s polling still VERIFYING → frontend shows timeout error
================================================================================
"""

import threading
import time
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel


from app.database import SessionLocal, get_db
from app.models import (
    ClientMT5Account, License, LiveTrade,
    ClientSymbolSetting, AISymbol,
)
from app.ai.models.ai_trade_history import AITradeHistory
from app.ai.models.ai_market_state import AIMarketState

# Broker suggestions are optional — never let a broken import block the module
try:
    from app.config.brokers import BROKERS
except Exception:
    BROKERS = []


logger = logging.getLogger("client_mt5")

router = APIRouter(prefix="/api/client", tags=["Client MT5 & AI"])


# ============================================================================
# MT5 TERMINAL LOCK + VERIFICATION QUEUE
# ----------------------------------------------------------------------------
# Single MT5 terminal serves all users. _MT5_LOCK serializes terminal access
# across the API (this file) and the periodic verification worker.
#
# When a user submits credentials, we spawn a worker thread that acquires the
# lock, performs the verification, and writes the result to the database.
# ============================================================================
_MT5_LOCK = threading.Lock()
_MT5_LOCK_TIMEOUT = 12  # seconds — enough for retry if broker is slow

# Track which logins are currently being verified (to dedupe rapid resubmits)
_verifying_now: set = set()
_verifying_lock = threading.Lock()


def _do_verification(login: str, password: str, server: str) -> dict:
    """
    Connect to MT5, log in, capture account info. Pure function — no DB writes.
    Returns dict with ok / reason / account fields.
    """
    out = {"ok": False, "reason": ""}

    # =========================================================
    # HARD RESET MT5 SESSION
    # =========================================================

    try:

        mt5.shutdown()

        time.sleep(1)

    except Exception:

        pass

    if not mt5.initialize():

        out["reason"] = (

            f"mt5_reinitialize_failed_"
            f"{mt5.last_error()}"

        )

        return out

    if not _MT5_LOCK.acquire(timeout=_MT5_LOCK_TIMEOUT):
        out["reason"] = "mt5_terminal_busy"
        return out
    try:
        # Ensure terminal alive
        if not mt5.terminal_info():
            mt5.shutdown()
            time.sleep(0.5)
            if not mt5.initialize():
                out["reason"] = f"mt5_init_failed_{mt5.last_error()}"
                return out

        try:
            login_int = int(login)
        except (ValueError, TypeError):
            out["reason"] = "invalid_login_not_numeric"
            return out

        if not mt5.login(login_int, password=password, server=server):
            err = mt5.last_error()
            err_code = err[0] if isinstance(err, tuple) and len(err) > 0 else 0
            # Map common MT5 error codes to friendly messages
            err_msg = "mt5_login_failed"
            if err_code in (-6, 10004):
                err_msg = "invalid_credentials"
            elif err_code in (-2, -3):
                err_msg = "server_unreachable"
            elif err_code == -8:
                err_msg = "account_disabled"
            out["reason"] = f"{err_msg}_{err}"
            return out

        time.sleep(0.5)
        info = mt5.account_info()
        if not info:
            out["reason"] = "account_info_unavailable"
            return out

        if str(info.login) != str(login):
            out["reason"] = f"account_mismatch_{login}_vs_{info.login}"
            return out

        out["ok"] = True
        out["account_name"] = str(info.name or "")
        out["broker_name"]  = str(info.company or "")
        out["balance"]      = float(info.balance or 0)
        out["equity"]       = float(info.equity or 0)
        out["currency"]     = str(info.currency or "USD")
        out["leverage"]     = int(info.leverage or 0)
        return out
    except Exception as e:
        out["reason"] = f"exception_{type(e).__name__}_{str(e)[:80]}"
        return out
    finally:

        try:

            mt5.shutdown()

        except Exception:

            pass

        try:
            _MT5_LOCK.release()
        except RuntimeError:
            pass


def _verify_async(account_id: int, login: str, password: str, server: str) -> None:
    """
    Worker thread: verifies one account against MT5 and updates DB.
    Runs after the POST endpoint returns to the user.
    """
    key = login
    with _verifying_lock:
        if key in _verifying_now:
            logger.info("Skip duplicate verify for %s (already in progress)", login)
            return
        _verifying_now.add(key)

    try:
        logger.info("🔍 Async verify start: %s", login)
        result = _do_verification(login, password, server)

        db = SessionLocal()
        try:
            account = db.query(ClientMT5Account).filter(
                ClientMT5Account.id == account_id
            ).first()
            if not account:
                logger.warning("Account row %d disappeared mid-verify", account_id)
                return

            if result["ok"]:
                account.is_verified         = True
                account.verification_status = "VERIFIED"
                account.account_name        = result["account_name"]
                account.broker_name         = result["broker_name"]
                account.balance             = result["balance"]
                account.equity              = result["equity"]
                account.last_verified_at    = datetime.now(timezone.utc)
                # CLEANUP #1: auto-enable AI trading the moment the account
                # verifies. User connects → account verifies → AI trades.
                # No separate "Start AI" tap required.
                account.ai_auto_trade       = True
                db.commit()
                logger.info(
                    "✅ ASYNC VERIFY %s | %s | %s | bal=$%.2f | AI auto-enabled",
                    login, result["account_name"], result["broker_name"],
                    result["balance"],
                )
            else:
                account.is_verified         = False
                account.verification_status = "FAILED"
                account.last_verified_at    = datetime.now(timezone.utc)
                if hasattr(account, "verification_error"):
                    account.verification_error = result["reason"][:200]
                db.commit()
                logger.warning("❌ ASYNC VERIFY FAILED %s | %s", login, result["reason"])
        except Exception as e:
            logger.error("DB write failed for %s: %s", login, e)
            try: db.rollback()
            except Exception: pass
        finally:
            try: db.close()
            except Exception: pass
    finally:
        with _verifying_lock:
            _verifying_now.discard(key)


# ============================================================================
# RISK LEVEL NORMALIZATION
# ============================================================================
def normalize_risk_level(value: Optional[str]) -> str:
    if not value:
        return "medium"
    v = value.lower().strip()
    aliases = {
        "low": "normal", "conservative": "normal", "safe": "normal",
        "normal": "normal",
        "medium": "medium", "balanced": "medium", "default": "medium",
        "aggressive": "aggressive", "high": "aggressive", "max": "aggressive",
    }
    return aliases.get(v, "medium")


# ============================================================================
# PYDANTIC MODELS
# ============================================================================
class MT5AccountCreate(BaseModel):
    license_key: str
    login: str
    password: str
    server: str
    risk_level: str = "medium"


class AISettingsUpdate(BaseModel):
    license_key: str
    symbols: List[str] = []


class SaveSymbolsRequest(BaseModel):
    license_key: str
    symbols: List[str]


# ============================================================================
# BROKERS LIST
# ----------------------------------------------------------------------------
# CLEANUP #2: NO broker restrictions. The list is SUGGESTIONS ONLY — users can
# type any server name. The save endpoint never validates server against this
# list. If the BROKERS import is empty/broken, we still return a working
# response so the frontend never blocks the user.
# ============================================================================
@router.get("/ai/brokers")
def get_brokers():
    try:
        suggestions = BROKERS if isinstance(BROKERS, (list, dict)) else []
    except Exception:
        suggestions = []
    return {
        "success": True,
        "brokers": suggestions,           # suggestions only — not enforced
        "custom_server_allowed": True,    # any server name accepted
        "restrictions": False,
    }


# ============================================================================
# SAVE MT5 ACCOUNT
# ----------------------------------------------------------------------------
# Returns IMMEDIATELY with status=VERIFYING. Background thread does the work.
# Frontend polls /ai/mt5-status every 3s to detect completion.
# ============================================================================
@router.post("/mt5-account")
def save_mt5_account(data: MT5AccountCreate, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(
        License.license_key == data.license_key
    ).first()
    if not license_row:
        raise HTTPException(status_code=400, detail="Invalid license key")

    risk_level = normalize_risk_level(data.risk_level)

    account = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()

    if account:
        account.login      = data.login
        account.password   = data.password
        account.server     = data.server
        account.risk_level = risk_level
        account.is_active  = True
        account.is_verified         = False
        account.verification_status = "VERIFYING"
    else:
        account = ClientMT5Account(
            license_id          = license_row.id,
            login               = data.login,
            password            = data.password,
            server              = data.server,
            risk_level          = risk_level,
            is_active           = True,
            ai_auto_trade       = False,
            is_verified         = False,
            verification_status = "VERIFYING",
        )
        db.add(account)
    db.commit()
    db.refresh(account)

    # Kick off async verification — returns immediately
    thread = threading.Thread(
        target=_verify_async,
        args=(account.id, data.login, data.password, data.server),
        daemon=True,
        name=f"verify-{data.login}",
    )
    thread.start()

    logger.info("📥 Queued verification: login=%s server=%s", data.login, data.server)

    return {
        "success":  True,
        "verified": False,
        "status":   "VERIFYING",
        "message":  "Verification in progress. Poll /ai/mt5-status for result.",
        "account": {
            "login":      account.login,
            "server":     account.server,
            "risk_level": account.risk_level,
        },
    }


# ============================================================================
# MT5 STATUS — frontend polls this every 3s
# ============================================================================
@router.get("/ai/mt5-status")
def get_mt5_status(license_key: str, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(
        License.license_key == license_key
    ).first()
    if not license_row:
        return {"connected": False, "status": "NO_LICENSE"}

    account = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()
    if not account:
        return {"connected": False, "status": "NOT_CONNECTED"}

    status = (account.verification_status or "PENDING").upper()
    return {
        "connected":           True,
        "status":              status,
        "login":               account.login,
        "account_name":        account.account_name,
        "broker":              account.broker_name,
        "server":              account.server,
        "balance":             float(account.balance or 0),
        "equity":              float(account.equity or 0),
        "verified":            bool(account.is_verified),
        "verification_status": status,
        "last_verified_at": (
            account.last_verified_at.isoformat()
            if account.last_verified_at else None
        ),
        "risk_level":    account.risk_level or "medium",
        "ai_auto_trade": bool(account.ai_auto_trade),
        "verification_error": (
            getattr(account, "verification_error", None)
            if status == "FAILED" else None
        ),
    }


# ============================================================================
# CLEANUP #4: RISK LEVEL UPDATE
# ----------------------------------------------------------------------------
# Lets the user change risk mode (normal/medium/aggressive) WITHOUT re-entering
# MT5 credentials. The old flow only saved risk_level inside /mt5-account, so
# changing it in the dashboard alone never persisted.
# ============================================================================
class RiskLevelUpdate(BaseModel):
    license_key: str
    risk_level: str


@router.post("/ai/risk-level")
def update_risk_level(data: RiskLevelUpdate, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(
        License.license_key == data.license_key
    ).first()
    if not license_row:
        raise HTTPException(status_code=400, detail="Invalid license key")

    account = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="No MT5 account connected")

    normalized = normalize_risk_level(data.risk_level)
    account.risk_level = normalized
    db.commit()
    logger.info("⚙️ Risk level updated: login=%s → %s", account.login, normalized)

    return {
        "success": True,
        "risk_level": normalized,
        "message": f"Risk level set to {normalized}",
    }


# ============================================================================
# CLEANUP #3: LIVE BALANCE REFRESH
# ----------------------------------------------------------------------------
# The stale-balance problem: balance was only captured at verify time. This
# endpoint does a FRESH MT5 read on demand (serialized by the terminal lock)
# so the dashboard can show the real, current balance/equity. Frontend can
# call this on dashboard open or via a manual "refresh balance" action.
#
# NOTE: don't call this every poll for 100+ users — it hits the MT5 terminal.
# Use it on dashboard load and on user-initiated refresh only.
# ============================================================================
@router.post("/ai/refresh-balance")
def refresh_balance(license_key: str, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(
        License.license_key == license_key
    ).first()
    if not license_row:
        raise HTTPException(status_code=400, detail="Invalid license key")

    account = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="No MT5 account connected")

    if not account.is_verified:
        return {
            "success": False,
            "message": "Account not verified yet",
            "balance": float(account.balance or 0),
            "equity":  float(account.equity or 0),
        }

    # Fresh read from MT5
    result = _do_verification(account.login, account.password, account.server)
    if result["ok"]:
        account.balance          = result["balance"]
        account.equity           = result["equity"]
        account.account_name     = result["account_name"]
        account.broker_name      = result["broker_name"]
        account.last_verified_at = datetime.now(timezone.utc)
        db.commit()
        return {
            "success":      True,
            "balance":      result["balance"],
            "equity":       result["equity"],
            "account_name": result["account_name"],
            "broker":       result["broker_name"],
            "currency":     result["currency"],
            "updated_at":   account.last_verified_at.isoformat(),
        }
    else:
        # Return last-known values if the live read fails
        return {
            "success": False,
            "message": result["reason"],
            "balance": float(account.balance or 0),
            "equity":  float(account.equity or 0),
        }


# ============================================================================
# AI SETTINGS — preserves per-symbol customizations
# ============================================================================
@router.post("/ai/settings")
def save_ai_settings(data: AISettingsUpdate, db: Session = Depends(get_db)):
    """
    UPSERT user's enabled symbols. Symbols removed from the list are disabled
    (not deleted) so per-symbol lot_size / max_open_trades / trade_direction
    customizations come back if the symbol is re-added later.
    """
    license_row = db.query(License).filter(
        License.license_key == data.license_key
    ).first()
    if not license_row:
        raise HTTPException(status_code=400, detail="Invalid license key")

    if len(data.symbols) > 50:
        raise HTTPException(status_code=400, detail="Too many symbols (max 50)")

    existing = {
        s.symbol_name: s for s in db.query(ClientSymbolSetting).filter(
            ClientSymbolSetting.license_id == license_row.id
        ).all()
    }
    new_set = {s.upper() for s in data.symbols}

    for sym_name, row in existing.items():
        if sym_name not in new_set:
            row.enabled = False

    for sym in new_set:
        if sym in existing:
            existing[sym].enabled = True
        else:
            db.add(ClientSymbolSetting(
                license_id      = license_row.id,
                symbol_name     = sym,
                enabled         = True,
                trade_direction = "both",
            ))
    db.commit()
    return {"success": True, "message": "Symbols updated"}


@router.get("/ai/settings")
def get_ai_settings(license_key: str, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(
        License.license_key == license_key
    ).first()
    if not license_row:
        raise HTTPException(status_code=404, detail="License not found")

    settings = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == license_row.id,
        ClientSymbolSetting.enabled == True,
    ).all()

    return {
        "success": True,
        "symbols": [
            {
                "symbol":          s.symbol_name,
                "lot_size":        float(s.lot_size) if s.lot_size else None,
                "max_open_trades": int(s.max_open_trades) if s.max_open_trades else None,
                "trade_direction": s.trade_direction or "both",
            }
            for s in settings
        ],
    }


# ============================================================================
# SYMBOLS (same upsert pattern)
# ============================================================================
@router.post("/ai/symbols")
def save_ai_symbols(data: SaveSymbolsRequest, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(
        License.license_key == data.license_key
    ).first()
    if not license_row:
        raise HTTPException(status_code=404, detail="License not found")

    existing = {
        s.symbol_name: s for s in db.query(ClientSymbolSetting).filter(
            ClientSymbolSetting.license_id == license_row.id
        ).all()
    }
    new_set = {s.upper() for s in data.symbols}

    for sym_name, row in existing.items():
        row.enabled = (sym_name in new_set)
    for sym in new_set:
        if sym not in existing:
            db.add(ClientSymbolSetting(
                license_id      = license_row.id,
                symbol_name     = sym,
                enabled         = True,
                trade_direction = "both",
            ))
    db.commit()
    return {"success": True, "message": "Symbols saved (settings preserved)"}


@router.get("/ai/symbols")
def get_ai_symbols(db: Session = Depends(get_db)):
    markets = db.query(AIMarketState).limit(50).all()
    return {
        "success": True,
        "symbols": [
            {
                "symbol":      m.symbol,
                "direction":   m.trend,
                "signal":      m.signal,
                "strength":    m.confidence,
                "entry_price": m.entry,
                "stop_loss":   m.stop_loss,
                "take_profit": m.take_profit,
                "analysis":    m.analysis,
                "updated_at":  m.updated_at.isoformat() if m.updated_at else None,
            }
            for m in markets
        ],
    }


# ============================================================================
# LIVE TRADES / HISTORY / STATS / MARKET DATA
# ============================================================================
# ============================================================================
# LIVE TRADES / HISTORY / STATS / MARKET DATA
# ============================================================================

# CLEANUP #5: any LiveTrade still marked OPEN after this many hours is almost
# certainly a ghost (closed on MT5 but the worker never reconciled it, e.g.
# closed manually, or from a previous worker version). A real scalp never
# stays open this long. We auto-mark these CLOSED so they stop cluttering the
# dashboard. Tune via env if needed.
import os as _os
STALE_OPEN_TRADE_HOURS = int(_os.environ.get("STALE_OPEN_TRADE_HOURS", "24"))


@router.get("/ai/live-trades")
def get_live_trades(license_key: str, db: Session = Depends(get_db)):
    from datetime import timedelta

    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc - timedelta(hours=STALE_OPEN_TRADE_HOURS)

    open_trades = db.query(LiveTrade).filter(
        LiveTrade.license_key == license_key,
        LiveTrade.status == "OPEN",
    ).order_by(LiveTrade.id.desc()).all()

    fresh = []
    stale_count = 0
    for t in open_trades:
        opened = t.opened_at
        # Normalize naive datetimes to UTC for comparison
        if opened is not None and opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)

        if opened is not None and opened < cutoff:
            # Ghost trade — auto-close it in the DB so it stops showing
            t.status = "CLOSED"
            if hasattr(t, "closed_at") and not t.closed_at:
                t.closed_at = now_utc
            stale_count += 1
            continue
        fresh.append(t)

    if stale_count:
        try:
            db.commit()
            logger.info("🧹 Auto-cleaned %d stale OPEN trade(s) for %s",
                       stale_count, license_key)
        except Exception:
            db.rollback()

    return [
        {
            "id":          t.id,
            "symbol":      t.symbol,
            "trade_type":  t.trade_type,
            "lot_size":    float(t.lot_size or 0),
            "entry_price": float(t.entry_price or 0),
            "stop_loss":   float(t.stop_loss or 0) if t.stop_loss else None,
            "take_profit": float(t.take_profit or 0) if t.take_profit else None,
            "profit":      round(float(t.profit or 0), 2),
            "status":      t.status,
            "mt5_ticket":  t.mt5_ticket,
            "opened_at":   t.opened_at.isoformat() if t.opened_at else None,
        }
        for t in fresh
    ]


@router.get("/ai/trade-history")
def get_trade_history(license_key: str, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(
        License.license_key == license_key
    ).first()
    if not license_row:
        return {"total_trades": 0, "trades": []}

    # Only show CLOSED trades — open/pending rows have no meaningful outcome.
    # This prevents the "all OPEN +0.00" noise seen in older data.
    trades = db.query(AITradeHistory).filter(
        AITradeHistory.license_id == license_row.id,
        AITradeHistory.status == "CLOSED",
    ).order_by(AITradeHistory.id.desc()).limit(500).all()

    return {
        "total_trades": len(trades),
        "trades": [
            {
                "symbol":     t.symbol,
                "trade_type": t.signal,
                "profit":     round(float(t.profit or 0), 2),
                "result":     t.result,          # WIN / LOSS / BREAKEVEN
                "status":     t.result,          # frontend shows this as the badge
                "lot_size":   float(t.lot_size or 0),
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "closed_at":  t.closed_at.isoformat() if t.closed_at else None,
            }
            for t in trades
        ],
    }


@router.get("/ai/signals-pro")
def get_signals_pro(license_key: str, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(
        License.license_key == license_key
    ).first()
    if not license_row:
        raise HTTPException(status_code=404, detail="License not found")

    # Only CLOSED trades count toward stats. Open/pending rows (profit 0,
    # no result) are excluded so we never show contradictory numbers like
    # "60% win rate but 0/0 wins/losses".
    trades = db.query(AITradeHistory).filter(
        AITradeHistory.license_id == license_row.id,
        AITradeHistory.status == "CLOSED",
    ).all()

    total = len(trades)

    # Classify by ACTUAL profit (single source of truth) rather than mixing
    # a "result" string field with a separate profit test.
    wins   = sum(1 for t in trades if float(t.profit or 0) > 0)
    losses = sum(1 for t in trades if float(t.profit or 0) < 0)
    breakeven = total - wins - losses
    net = round(sum(float(t.profit or 0) for t in trades), 2)

    # Win rate = wins / decisive trades (exclude breakevens from the ratio)
    decisive = wins + losses
    win_rate = round((wins / decisive * 100), 1) if decisive > 0 else 0.0

    return {
        "total_trades": total,
        "wins":         wins,
        "losses":       losses,
        "breakeven":    breakeven,
        "win_rate":     win_rate,
        "net_profit":   net,
    }


@router.get("/ai/status")
def ai_status(db: Session = Depends(get_db)):
    pairs = db.query(AISymbol).filter(AISymbol.enabled == True).count()
    return {"ai_active": True, "pairs_tracked": pairs}


@router.get("/ai/market-data")
def get_market_data(symbol: str, db: Session = Depends(get_db)):
    market = db.query(AIMarketState).filter(
        AIMarketState.symbol == symbol.upper()
    ).order_by(AIMarketState.updated_at.desc()).first()

    if not market:
        return {
            "success": False, "symbol": symbol, "direction": "NEUTRAL",
            "signal": "NONE", "entry_price": 0, "strength": 0,
            "analysis": "", "stop_loss": 0, "take_profit": 0,
            "updated_at": None,
        }
    return {
        "success":     True,
        "symbol":      market.symbol,
        "direction":   market.trend,
        "signal":      market.signal,
        "entry_price": float(market.entry or 0),
        "strength":    int(market.confidence or 0),
        "analysis":    market.analysis,
        "stop_loss":   float(market.stop_loss or 0) if market.stop_loss else None,
        "take_profit": float(market.take_profit or 0) if market.take_profit else None,
        "updated_at":  market.updated_at.isoformat() if market.updated_at else None,
    }