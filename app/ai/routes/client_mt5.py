"""
================================================================================
  CLIENT MT5 + AI API ENDPOINTS  (RENDER / LINUX — NO MetaTrader5)
================================================================================
  IMPORTANT: This file runs on Render (Linux). It must NEVER import or use
  the MetaTrader5 package — that package is Windows-only and crashes Render
  on deploy. ALL MT5 work (login, balance, verification) is done by the
  Windows-side client_mt5_verification_worker.py. The database is the message
  bus between them.

  CONNECT FLOW:
    1. POST /mt5-account
         - Saves credentials to DB
         - Sets verification_status = "VERIFYING"
         - Returns immediately (~50ms). Does NOT touch MT5.
    2. Windows worker (fast loop) sees the VERIFYING row within ~1-2s,
       logs in, captures balance/name/broker, writes VERIFIED + details,
       and auto-enables ai_auto_trade.
    3. Frontend polls GET /ai/mt5-status every 3s → sees VERIFIED + details.
       Feels instant (details appear in ~3-5 seconds).

  REFRESH FLOW:
    - POST /ai/refresh-balance just flags the account (status → REFRESH)
      so the worker re-reads balance on its next loop. Frontend polls status.
================================================================================
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import os

from fastapi import APIRouter, Depends, HTTPException, Body, Header
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel

from app.database import get_db
from app.models import (
    ClientMT5Account, License, LiveTrade,
    ClientSymbolSetting, AISymbol,
)
from app.ai.models.ai_trade_history import AITradeHistory
from app.ai.models.ai_market_state import AIMarketState


logger = logging.getLogger("client_mt5")

router = APIRouter(prefix="/api/client", tags=["Client MT5 & AI"])

# Admin router — destructive cleanup operations, guarded by a shared token.
admin_router = APIRouter(prefix="/api/admin", tags=["Admin"])

# Set NOLIMITZ_ADMIN_TOKEN in your environment (Render dashboard → Environment).
# If unset, admin endpoints are DISABLED (return 503) so they can't be abused
# with a blank token.
_ADMIN_TOKEN = os.environ.get("NOLIMITZ_ADMIN_TOKEN", "")


def require_admin(x_admin_token: str = Header(default="")):
    """Guard for destructive admin endpoints. Requires the X-Admin-Token header
    to match NOLIMITZ_ADMIN_TOKEN. Disabled entirely if the env var is unset."""
    if not _ADMIN_TOKEN:
        raise HTTPException(status_code=503,
                            detail="Admin API disabled (NOLIMITZ_ADMIN_TOKEN not set)")
    if x_admin_token != _ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")
    return True



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
    risk_level: Optional[str] = None   # optional — if sent, also update mode


class SaveSymbolsRequest(BaseModel):
    license_key: str
    symbols: List[str]


class RiskLevelUpdate(BaseModel):
    license_key: str
    risk_level: str


# ============================================================================
# DISPLAY-ONLY MODE TABLES
# These MIRROR the execution worker's MODE_LOTS / RISK_MODE so the dashboard
# can show the EFFECTIVE lot size and trade count the worker will actually
# use for each symbol under the selected mode. The worker remains the source
# of truth for execution; these are only for display. Keep them in sync with
# execution_worker.py cfg.MODE_LOTS / RISK_MODE.
# ============================================================================
_DISPLAY_MODE_LOTS = {
    "normal":     {"GOLD": 0.01, "BTC": 0.10, "ETH": 0.10, "INDEX": 0.05,
                   "OIL": 0.05, "FOREX": 0.05, "JPY": 0.05, "OTHER": 0.02},
    "medium":     {"GOLD": 0.02, "BTC": 0.20, "ETH": 0.20, "INDEX": 0.10,
                   "OIL": 0.10, "FOREX": 0.10, "JPY": 0.10, "OTHER": 0.05},
    "aggressive": {"GOLD": 0.05, "BTC": 0.50, "ETH": 0.50, "INDEX": 0.25,
                   "OIL": 0.25, "FOREX": 0.20, "JPY": 0.20, "OTHER": 0.10},
}
_DISPLAY_MODE_MAX_TRADES = {"normal": 2, "medium": 3, "aggressive": 4}


def _display_classify(symbol: str) -> str:
    s = (symbol or "").upper()
    if "XAU" in s or "GOLD" in s:           return "GOLD"
    if "BTC" in s:                          return "BTC"
    if "ETH" in s:                          return "ETH"
    if "OIL" in s or "WTI" in s or "USOIL" in s: return "OIL"
    if any(x in s for x in ("US30", "NAS", "SPX", "GER", "UK100", "JP225")): return "INDEX"
    if "JPY" in s:                          return "JPY"
    if len(s) == 6 and s.isalpha():         return "FOREX"
    return "OTHER"


def _effective_lot_and_trades(symbol: str, risk_level: str):
    """Return (lot_size, max_open_trades) the worker will actually use for
    this symbol under the given mode — matching the worker's mode_first logic."""
    mode = (risk_level or "medium").lower()
    if mode not in _DISPLAY_MODE_LOTS:
        mode = "medium"
    cls = _display_classify(symbol)
    lot = _DISPLAY_MODE_LOTS[mode].get(cls, _DISPLAY_MODE_LOTS[mode]["OTHER"])
    trades = _DISPLAY_MODE_MAX_TRADES.get(mode, 3)
    return lot, trades


# ============================================================================
# BROKER LIST — no restrictions, any server allowed. Suggestions come from
# the frontend; backend never enforces a broker list.
# ============================================================================
@router.get("/brokers")
def get_brokers():
    return {
        "success": True,
        "brokers": [],                  # no backend-enforced list
        "custom_server_allowed": True,  # any server name accepted
        "restrictions": False,
    }


# ============================================================================
# SAVE MT5 ACCOUNT — saves creds, sets VERIFYING. Worker does the rest.
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
        if hasattr(account, "verification_error"):
            account.verification_error = None  # clear stale error on resubmit
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
@router.get("/mt5-status")
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
# RISK LEVEL UPDATE — change mode without re-entering MT5 creds
# ============================================================================
@router.post("/risk-level")
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
# START / STOP AI — toggle auto-trading on the user's account
# ----------------------------------------------------------------------------
# Stop AI sets ai_auto_trade=False so the execution worker stops opening NEW
# trades for this user. Existing open positions continue being managed (BE
# lock, partials, trailing) until they close naturally — same way a pro
# trader would close out positions cleanly. Use /stop-ai-now if a user wants
# everything force-closed (not implemented here — needs MT5 access).
# ============================================================================
class AIToggle(BaseModel):
    license_key: str


@router.post("/start-ai")
def start_ai(data: AIToggle, db: Session = Depends(get_db)):
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
    if not account.is_verified:
        raise HTTPException(status_code=400, detail="MT5 account not verified")

    account.ai_auto_trade = True
    db.commit()
    logger.info("▶️ AI STARTED: login=%s", account.login)
    return {
        "success": True,
        "ai_auto_trade": True,
        "message": "AI auto-trading started",
    }


@router.post("/stop-ai")
def stop_ai(data: AIToggle, db: Session = Depends(get_db)):
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

    account.ai_auto_trade = False
    db.commit()
    logger.info("⏹️ AI STOPPED: login=%s (open positions will continue being managed until they close)",
               account.login)
    return {
        "success": True,
        "ai_auto_trade": False,
        "message": "AI stopped. No new trades will open. Existing positions continue being managed until close.",
    }


@router.get("/ai-state")
def get_ai_state(license_key: str, db: Session = Depends(get_db)):
    """Quick check whether AI is currently running for this user."""
    license_row = db.query(License).filter(
        License.license_key == license_key
    ).first()
    if not license_row:
        return {"ai_auto_trade": False, "connected": False}

    account = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()
    if not account:
        return {"ai_auto_trade": False, "connected": False}

    return {
        "connected":     True,
        "ai_auto_trade": bool(account.ai_auto_trade),
        "verified":      bool(account.is_verified),
        "status":        account.verification_status or "PENDING",
    }


# ============================================================================
# REFRESH BALANCE — flags account so the Windows worker re-reads balance.
# (No MT5 here — just sets status so the worker refreshes on its next loop.)
# ============================================================================
@router.post("/refresh-balance")
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

    # Flag for worker to re-read on next loop. Worker picks REFRESH up fast.
    account.verification_status = "REFRESH"
    db.commit()

    return {
        "success": True,
        "message": "Balance refresh queued",
        "balance": float(account.balance or 0),   # last-known, until worker updates
        "equity":  float(account.equity or 0),
        "status":  "REFRESH",
    }


# ============================================================================
# AI SETTINGS — preserves per-symbol customizations
# ============================================================================
@router.post("/settings")
def save_ai_settings(data: AISettingsUpdate, db: Session = Depends(get_db)):
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

    # FIX 1: if risk_level was sent, save it on the MT5 account too, so the
    # user can change mode from the AI settings screen and it persists.
    saved_risk = None
    if data.risk_level:
        account = db.query(ClientMT5Account).filter(
            ClientMT5Account.license_id == license_row.id
        ).first()
        if account:
            saved_risk = normalize_risk_level(data.risk_level)
            account.risk_level = saved_risk

    db.commit()
    return {
        "success": True,
        "message": "Symbols updated",
        "risk_level": saved_risk,
    }


@router.get("/settings")
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

    # Include the saved risk mode so the frontend can restore it after refresh
    account = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()
    risk_level = (account.risk_level if account and account.risk_level else "medium")

    # Return the EFFECTIVE lot_size / max_open_trades the worker will actually
    # use for each symbol under the current mode — NOT the raw stored defaults
    # (which are 0.01 / 1 from the frontend and get overridden by mode_first
    # in the worker). This makes the dashboard show the truth: e.g. medium BTC
    # = 0.20 lot / 3 trades, medium XAU = 0.02 lot / 3 trades.
    out_symbols = []
    for s in settings:
        eff_lot, eff_trades = _effective_lot_and_trades(s.symbol_name, risk_level)
        out_symbols.append({
            "symbol":          s.symbol_name,
            "lot_size":        eff_lot,
            "max_open_trades": eff_trades,
            "trade_direction": s.trade_direction or "both",
        })

    return {
        "success": True,
        "risk_level": risk_level,
        "symbols": out_symbols,
    }


# ============================================================================
# SYMBOLS (same upsert pattern)
# ============================================================================
@router.post("/symbols")
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


@router.get("/symbols")
def get_ai_symbols(license_key: str = None, db: Session = Depends(get_db)):
    """
    SCANNER FEED — always returns the full scanned market catalog
    (AIMarketState) so the scanner shows ALL symbols the watcher is tracking,
    for everyone, with or without a license key.

    NOTE: license_key is accepted for backwards-compatibility but intentionally
    IGNORED here — the scanner is a free, show-everything feed. The settings
    panel gets the user's OWN enabled symbols from GET /settings, not from this
    endpoint, so this endpoint never needs to be license-scoped.
    """
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
# LIVE TRADES — with auto-cleanup of stale OPEN rows
# ============================================================================
import os as _os
STALE_OPEN_TRADE_HOURS = int(_os.environ.get("STALE_OPEN_TRADE_HOURS", "24"))


@router.get("/live-trades")
def get_live_trades(license_key: str, db: Session = Depends(get_db)):
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
        if opened is not None and opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        if opened is not None and opened < cutoff:
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


# ============================================================================
# TRADE HISTORY — only CLOSED trades with real outcomes
# ============================================================================
@router.get("/closed-trades")
def get_trade_history(license_key: str, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(
        License.license_key == license_key
    ).first()
    if not license_row:
        return {"total_trades": 0, "trades": []}

    # Show ALL AI-executed closed trades for this license. The data is kept
    # clean by (a) the corrected write_trade_outcome (close-deals-only +
    # sanity cap) so values match MT5, and (b) the /reset-history endpoint
    # the operator calls at launch to clear any legacy/buggy rows. We do NOT
    # filter by last_verified_at — that timestamp moves on every reconnect
    # and would make a user's history vanish after reconnecting.
    trades = db.query(AITradeHistory).filter(
        AITradeHistory.license_id == license_row.id,
        AITradeHistory.status == "CLOSED",
    ).order_by(AITradeHistory.id.desc()).limit(500).all()

    return {
        "total_trades": len(trades),
        "trades": [
            {
                "symbol":      t.symbol,
                "trade_type":  t.signal,
                "profit":      round(float(t.profit or 0), 2),
                "result":      t.result,
                "status":      t.result,
                "lot_size":    float(t.lot_size or 0),
                "entry_price": float(t.entry_price) if t.entry_price else None,
                "close_price": None,  # not stored; MT5 settles the exit
                "confidence":  int(t.confidence) if getattr(t, "confidence", None) else None,
                "created_at":  t.created_at.isoformat() if t.created_at else None,
                "closed_at":   t.closed_at.isoformat() if t.closed_at else None,
            }
            for t in trades
        ],
    }


# ============================================================================
# SIGNALS PRO — stats from all AI-executed trades for this license
# ============================================================================
@router.get("/signals-pro")
def get_signals_pro(license_key: str, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(
        License.license_key == license_key
    ).first()
    if not license_row:
        raise HTTPException(status_code=404, detail="License not found")

    # Same scope as /closed-trades — all CLOSED AI trades for this license.
    trades = db.query(AITradeHistory).filter(
        AITradeHistory.license_id == license_row.id,
        AITradeHistory.status == "CLOSED",
    ).all()

    total = len(trades)
    wins   = sum(1 for t in trades if float(t.profit or 0) > 0)
    losses = sum(1 for t in trades if float(t.profit or 0) < 0)
    breakeven = total - wins - losses
    net = round(sum(float(t.profit or 0) for t in trades), 2)

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


# ============================================================================
# AI STATUS / MARKET DATA
# ============================================================================
@router.get("/history-debug")
def history_debug(license_key: str, db: Session = Depends(get_db)):
    """
    Diagnostic: shows exactly what is stored for this license so you can
    confirm whether old/garbage rows still exist and whether they're scoped
    to the right license_id. Safe, read-only.
    """
    license_row = db.query(License).filter(
        License.license_key == license_key
    ).first()
    if not license_row:
        return {"found": False, "reason": "license_not_found", "license_key": license_key}

    rows = db.query(AITradeHistory).filter(
        AITradeHistory.license_id == license_row.id
    ).order_by(AITradeHistory.id.desc()).limit(20).all()

    total = db.query(AITradeHistory).filter(
        AITradeHistory.license_id == license_row.id
    ).count()
    # Also count any orphan rows (no license_id) that could leak via bad joins
    orphans = db.query(AITradeHistory).filter(
        AITradeHistory.license_id.is_(None)
    ).count()

    return {
        "found": True,
        "license_id": license_row.id,
        "total_rows_for_this_license": total,
        "orphan_rows_null_license": orphans,
        "sample_recent": [
            {
                "id": t.id, "symbol": t.symbol, "signal": t.signal,
                "profit": float(t.profit or 0), "result": t.result,
                "status": t.status,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            }
            for t in rows
        ],
    }


@router.post("/reset-history")
def reset_trade_history(license_key: str = None, body: dict = Body(default=None),
                        db: Session = Depends(get_db)):
    """
    PRODUCTION CLEAN SLATE.

    Deletes ALL AITradeHistory rows for this license. Accepts the license key
    either as a query param (?license_key=...) OR in the JSON body
    ({"license_key": "..."}) so it works no matter how the client sends it.

    Use this once at launch (or whenever you want a fresh start) to clear out
    trades recorded by older, buggy worker versions whose profit values don't
    match MT5. After this, only NEW trades the execution worker records — using
    the corrected close-deal-only + sanity-capped profit logic — will appear,
    so Trade History and Signals Pro match MT5.
    """
    key = license_key or (body or {}).get("license_key")
    if not key:
        raise HTTPException(status_code=422, detail="license_key required (query or body)")

    license_row = db.query(License).filter(
        License.license_key == key
    ).first()
    if not license_row:
        raise HTTPException(status_code=404, detail="License not found")

    deleted = db.query(AITradeHistory).filter(
        AITradeHistory.license_id == license_row.id
    ).delete(synchronize_session=False)
    db.commit()

    return {
        "success": True,
        "deleted": int(deleted or 0),
        "message": "Trade history cleared. Only new AI trades will show from now on.",
    }


@router.get("/status")
def ai_status(db: Session = Depends(get_db)):
    pairs = db.query(AISymbol).filter(AISymbol.enabled == True).count()
    return {"ai_active": True, "pairs_tracked": pairs}


# ============================================================================
# SYMBOL CATALOG — categorized universe for the scanner / symbol picker.
# The frontend groups symbols by category and flags "recommended" ones.
# A symbol only produces live scanner cards + signals once it is enabled in
# the AISymbol table (which the watcher scans). The "live" flag below tells
# the UI which catalog entries are currently being scanned, so it can show
# the rest as "available — ask admin to enable" without breaking.
# ============================================================================
_SYMBOL_CATALOG = {
    "metals": {
        "label": "Metals",
        "symbols": [
            {"symbol": "XAUUSD", "name": "Gold",        "recommended": True},
            {"symbol": "XAGUSD", "name": "Silver",      "recommended": False},
            {"symbol": "XPTUSD", "name": "Platinum",    "recommended": False},
        ],
    },
    "crypto": {
        "label": "Crypto",
        "symbols": [
            {"symbol": "BTCUSD", "name": "Bitcoin",     "recommended": True},
            {"symbol": "ETHUSD", "name": "Ethereum",    "recommended": False},
            {"symbol": "XRPUSD", "name": "Ripple",      "recommended": False},
            {"symbol": "SOLUSD", "name": "Solana",      "recommended": False},
            {"symbol": "LTCUSD", "name": "Litecoin",    "recommended": False},
        ],
    },
    "forex": {
        "label": "Forex",
        "symbols": [
            {"symbol": "EURUSD", "name": "Euro / USD",       "recommended": True},
            {"symbol": "GBPUSD", "name": "Pound / USD",      "recommended": False},
            {"symbol": "USDJPY", "name": "USD / Yen",        "recommended": False},
            {"symbol": "AUDUSD", "name": "Aussie / USD",     "recommended": False},
            {"symbol": "USDCAD", "name": "USD / Loonie",     "recommended": False},
            {"symbol": "USDCHF", "name": "USD / Franc",      "recommended": False},
            {"symbol": "NZDUSD", "name": "Kiwi / USD",       "recommended": False},
            {"symbol": "EURJPY", "name": "Euro / Yen",       "recommended": False},
            {"symbol": "GBPJPY", "name": "Pound / Yen",      "recommended": False},
        ],
    },
    "indices": {
        "label": "Indices",
        "symbols": [
            {"symbol": "US30",   "name": "Dow Jones",   "recommended": False},
            {"symbol": "NAS100", "name": "Nasdaq 100",  "recommended": False},
            {"symbol": "SPX500", "name": "S&P 500",     "recommended": False},
            {"symbol": "GER40",  "name": "DAX 40",      "recommended": False},
        ],
    },
    "synthetic": {
        "label": "Synthetic",
        "symbols": [
            {"symbol": "V75",  "name": "Volatility 75 Index",  "recommended": False},
            {"symbol": "V100", "name": "Volatility 100 Index", "recommended": False},
            {"symbol": "BOOM1000", "name": "Boom 1000",        "recommended": False},
            {"symbol": "CRASH1000", "name": "Crash 1000",      "recommended": False},
        ],
    },
}


@router.get("/symbol-catalog")
def symbol_catalog(license_key: str = None, db: Session = Depends(get_db)):
    """
    Categorized symbol universe for the scanner / picker.

    Returns categories (Metals, Crypto, Forex, Indices, Synthetic), each with
    its symbols, a display name, a `recommended` flag (Gold + Bitcoin + EURUSD),
    a `live` flag (currently scanned by the watcher), and — when a license_key
    is supplied — an `enabled` flag showing which symbols this user has turned
    on for trading.
    """
    # Which symbols the watcher is actively scanning right now
    live_syms = {
        r.symbol.upper()
        for r in db.query(AISymbol).filter(AISymbol.enabled == True).all()
    }

    # Which symbols THIS user has enabled for trading
    user_enabled = set()
    if license_key:
        lic = db.query(License).filter(License.license_key == license_key).first()
        if lic:
            user_enabled = {
                s.symbol_name.upper()
                for s in db.query(ClientSymbolSetting).filter(
                    ClientSymbolSetting.license_id == lic.id,
                    ClientSymbolSetting.enabled == True,
                ).all()
            }

    categories = []
    for key, cat in _SYMBOL_CATALOG.items():
        syms = []
        for s in cat["symbols"]:
            up = s["symbol"].upper()
            syms.append({
                "symbol":      s["symbol"],
                "name":        s["name"],
                "recommended": s["recommended"],
                "live":        up in live_syms,
                "enabled":     up in user_enabled,
            })
        categories.append({
            "key":     key,
            "label":   cat["label"],
            "symbols": syms,
        })

    return {"success": True, "categories": categories}


@router.get("/market-data")
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


# ============================================================================
# FREE SCANNER — public, no license required.
# Returns every currently-scanned symbol's latest signal in ONE call, grouped
# by category, so free (non-paying) users can browse all live signals without
# connecting MT5 or owning a license. Trading still requires a license, but
# VIEWING signals is free. This is the upsell surface: show the signals, then
# prompt "connect MT5 to auto-trade these".
# ============================================================================
def _catalog_lookup():
    """symbol(upper) → (category_label, display_name, recommended)"""
    out = {}
    for key, cat in _SYMBOL_CATALOG.items():
        for s in cat["symbols"]:
            out[s["symbol"].upper()] = (cat["label"], s["name"], s["recommended"])
    return out


@router.get("/scanner")
def free_scanner(db: Session = Depends(get_db)):
    """
    Public scanner feed — no license_key needed. Lists the latest signal for
    every symbol the watcher is currently scanning, grouped by category.
    """
    lookup = _catalog_lookup()

    # All symbols the watcher is actively scanning
    live_syms = [
        r.symbol for r in db.query(AISymbol).filter(AISymbol.enabled == True).all()
    ]

    cards = []
    for sym in live_syms:
        up = sym.upper()
        market = db.query(AIMarketState).filter(
            AIMarketState.symbol == up
        ).order_by(AIMarketState.updated_at.desc()).first()
        if not market:
            continue
        label, name, recommended = lookup.get(up, ("Other", up, False))
        cards.append({
            "symbol":      market.symbol,
            "name":        name,
            "category":    label,
            "recommended": recommended,
            "direction":   market.trend,
            "signal":      market.signal,
            "entry_price": float(market.entry or 0),
            "strength":    int(market.confidence or 0),
            "analysis":    market.analysis,
            "stop_loss":   float(market.stop_loss or 0) if market.stop_loss else None,
            "take_profit": float(market.take_profit or 0) if market.take_profit else None,
            "updated_at":  market.updated_at.isoformat() if market.updated_at else None,
        })

    # Sort: recommended first, then by confidence desc
    cards.sort(key=lambda c: (not c["recommended"], -c["strength"]))

    return {
        "success": True,
        "count": len(cards),
        "free": True,
        "signals": cards,
    }

# ============================================================================
# ADMIN — ACCOUNT CLEANUP
# Destructive maintenance tools, guarded by X-Admin-Token. Use these to keep
# the worker efficient and the DB clean.
# ============================================================================
@admin_router.get("/accounts-overview")
def accounts_overview(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Snapshot of all MT5 accounts grouped by state, so you can see what would
    be cleared before doing it. Read-only.
    """
    accounts = db.query(ClientMT5Account).all()
    failed, inactive, verified, pending, dupes = [], [], [], [], []
    by_login = {}
    for a in accounts:
        status = (a.verification_status or "").upper()
        row = {
            "id": a.id, "login": a.login, "server": a.server,
            "status": status, "is_active": bool(a.is_active),
            "is_verified": bool(a.is_verified),
            "error": getattr(a, "verification_error", None),
            "last_verified_at": a.last_verified_at.isoformat() if a.last_verified_at else None,
        }
        if not a.is_active:
            inactive.append(row)
        elif status == "FAILED":
            failed.append(row)
        elif a.is_verified:
            verified.append(row)
        else:
            pending.append(row)
        by_login.setdefault(str(a.login), []).append(a.id)

    for login, ids in by_login.items():
        if len(ids) > 1:
            dupes.append({"login": login, "ids": ids})

    return {
        "total": len(accounts),
        "counts": {
            "failed": len(failed), "inactive": len(inactive),
            "verified": len(verified), "pending": len(pending),
            "duplicate_logins": len(dupes),
        },
        "failed": failed,
        "inactive": inactive,
        "duplicate_logins": dupes,
    }


@admin_router.post("/clear-failed-accounts")
def clear_failed_accounts(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Deactivate every account stuck in FAILED so the worker stops processing
    them. This is a SOFT clear: it sets is_active=False (the worker skips
    inactive accounts) but keeps the row, so the user can resubmit and
    reconnect later. Returns how many were cleared.
    """
    rows = db.query(ClientMT5Account).filter(
        ClientMT5Account.verification_status == "FAILED",
        ClientMT5Account.is_active == True,
    ).all()
    n = 0
    for a in rows:
        a.is_active = False
        n += 1
    db.commit()
    return {"success": True, "cleared": n,
            "message": f"Deactivated {n} failed account(s). Worker will stop processing them."}


@admin_router.post("/delete-inactive-accounts")
def delete_inactive_accounts(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Permanently DELETE all accounts that are is_active=False. Use after
    clear-failed (or for abandoned accounts) to keep the DB clean. This is a
    HARD delete — the rows are gone. Verified, active accounts are never
    touched. Returns how many were deleted.
    """
    deleted = db.query(ClientMT5Account).filter(
        ClientMT5Account.is_active == False
    ).delete(synchronize_session=False)
    db.commit()
    return {"success": True, "deleted": int(deleted or 0),
            "message": f"Deleted {int(deleted or 0)} inactive account(s)."}


@admin_router.post("/dedupe-accounts")
def dedupe_accounts(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Remove duplicate account rows that share the same login, keeping only the
    most-recently-verified one per login. Prevents the worker from double-
    trading the same MT5 account.
    """
    accounts = db.query(ClientMT5Account).all()
    by_login = {}
    for a in accounts:
        by_login.setdefault(str(a.login), []).append(a)

    removed = 0
    for login, rows in by_login.items():
        if len(rows) <= 1:
            continue
        # Keep the most-recently-verified (then highest id) row
        rows.sort(key=lambda r: (
            r.last_verified_at or __import__("datetime").datetime.min.replace(
                tzinfo=__import__("datetime").timezone.utc),
            r.id,
        ), reverse=True)
        for extra in rows[1:]:
            db.delete(extra)
            removed += 1
    db.commit()
    return {"success": True, "removed": removed,
            "message": f"Removed {removed} duplicate account row(s)."}