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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
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

    return {
        "success": True,
        "risk_level": risk_level,
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
                "result":     t.result,
                "status":     t.result,
                "lot_size":   float(t.lot_size or 0),
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "closed_at":  t.closed_at.isoformat() if t.closed_at else None,
            }
            for t in trades
        ],
    }


# ============================================================================
# SIGNALS PRO — stats only from CLOSED trades, profit as source of truth
# ============================================================================
@router.get("/signals-pro")
def get_signals_pro(license_key: str, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(
        License.license_key == license_key
    ).first()
    if not license_row:
        raise HTTPException(status_code=404, detail="License not found")

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
@router.get("/status")
def ai_status(db: Session = Depends(get_db)):
    pairs = db.query(AISymbol).filter(AISymbol.enabled == True).count()
    return {"ai_active": True, "pairs_tracked": pairs}


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
# DIAGNOSE — temporary endpoint to figure out why live-trades is empty
# ----------------------------------------------------------------------------
# Open in browser:
#   https://nolimitz-backend-yfne.onrender.com/api/client/diagnose-live-trades
#       ?license_key=NL-U42YHV54J5
#
# It returns a JSON report explaining exactly what the DB contains, so we
# can pinpoint why /live-trades returns []. REMOVE THIS ENDPOINT after the
# investigation — it doesn't require auth and exposes internal state.
# ============================================================================
@router.get("/diagnose-live-trades")
def diagnose_live_trades(license_key: str, db: Session = Depends(get_db)):
    report = {"license_key_input": license_key}

    # 1) Does the license exist?
    lic = db.query(License).filter(License.license_key == license_key).first()
    report["license_found"] = bool(lic)
    if not lic:
        report["hint"] = "License key not found at all. Check spelling."
        return report
    report["license_id"] = lic.id
    report["license_key_in_db"] = lic.license_key

    # 2) MT5 account info
    acct = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == lic.id
    ).first()
    if acct:
        report["mt5_account"] = {
            "login":        acct.login,
            "verified":     bool(acct.is_verified),
            "status":       acct.verification_status,
            "ai_auto_trade": bool(acct.ai_auto_trade),
            "risk_level":   acct.risk_level,
            "balance":      float(acct.balance or 0),
        }
    else:
        report["mt5_account"] = None

    # 3) Count LiveTrade rows by status FOR THIS license_key (exact match)
    from sqlalchemy import func
    by_status = (
        db.query(LiveTrade.status, func.count(LiveTrade.id))
        .filter(LiveTrade.license_key == license_key)
        .group_by(LiveTrade.status)
        .all()
    )
    report["live_trades_by_status_for_license_key"] = {s: n for s, n in by_status}

    # 4) Same count but search by mt5_login (catches mismatch case)
    if acct:
        by_login = (
            db.query(LiveTrade.status, func.count(LiveTrade.id))
            .filter(LiveTrade.mt5_login == str(acct.login))
            .group_by(LiveTrade.status)
            .all()
        )
        report["live_trades_by_status_for_mt5_login"] = {s: n for s, n in by_login}

    # 5) Recent rows (last 6 hours) regardless of license — see what worker writes
    six_hours_ago = datetime.now(timezone.utc) - timedelta(hours=6)
    recent = db.query(LiveTrade).filter(
        LiveTrade.opened_at > six_hours_ago
    ).order_by(LiveTrade.id.desc()).limit(15).all()
    report["recent_live_trades_any_license"] = [
        {
            "id":          t.id,
            "license_key": t.license_key,
            "mt5_login":   t.mt5_login,
            "symbol":      t.symbol,
            "status":      t.status,
            "ticket":      t.mt5_ticket,
            "lot":         float(t.lot_size or 0),
            "opened_at":   t.opened_at.isoformat() if t.opened_at else None,
        }
        for t in recent
    ]

    # 6) The actual filter used by GET /live-trades, replayed
    open_for_key = db.query(LiveTrade).filter(
        LiveTrade.license_key == license_key,
        LiveTrade.status == "OPEN",
    ).all()
    report["matches_live_trades_endpoint_filter"] = len(open_for_key)

    # 7) Verdict — narrow the cause
    n_by_key   = sum(report["live_trades_by_status_for_license_key"].values())
    n_by_login = sum(
        report.get("live_trades_by_status_for_mt5_login", {}).values()
    )
    if n_by_key == 0 and n_by_login == 0:
        report["verdict"] = "NO_ROWS_AT_ALL — the worker never wrote LiveTrade rows. Check the worker is running and writing to the same DB."
    elif n_by_key == 0 and n_by_login > 0:
        report["verdict"] = "LICENSE_KEY_MISMATCH — rows exist under mt5_login but with a different/null license_key. Worker writes broken license_key."
    elif report["live_trades_by_status_for_license_key"].get("OPEN", 0) == 0:
        report["verdict"] = "ALL_CLOSED — rows exist but none are OPEN. Either auto-cleanup ran, or worker closed them in DB."
    else:
        report["verdict"] = "OK — rows exist and should be returned. If endpoint still gives [], cache or wrong frontend URL."

    return report


# ============================================================================
# BACKFILL — one-shot migration to fix orphaned AITradeHistory rows
# ----------------------------------------------------------------------------
# Old worker wrote AITradeHistory with license_id=NULL because it tried to
# read signal.license_id (which doesn't exist). Worker is now fixed, but the
# existing orphaned rows are invisible to /trade-history and /signals-pro.
#
# This endpoint joins AITradeHistory → ClientMT5Account (via mt5_login) →
# License and backfills the license_id. Idempotent — safe to call multiple
# times. REMOVE AFTER RUNNING.
#
# Open in browser:
#   https://nolimitz-backend-yfne.onrender.com/api/client/backfill-history
# ============================================================================
@router.get("/backfill-history")
def backfill_history(db: Session = Depends(get_db)):
    # Build map: mt5_login → license_id, from ClientMT5Account
    accounts = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id.isnot(None)
    ).all()
    login_to_license = {str(a.login): a.license_id for a in accounts if a.login}

    if not login_to_license:
        return {"success": False, "reason": "no_accounts_found", "fixed": 0}

    # Find all orphaned AITradeHistory rows
    orphans = db.query(AITradeHistory).filter(
        AITradeHistory.license_id.is_(None),
        AITradeHistory.mt5_login.isnot(None),
    ).all()

    fixed = 0
    no_match = 0
    for row in orphans:
        lic_id = login_to_license.get(str(row.mt5_login))
        if lic_id:
            row.license_id = lic_id
            fixed += 1
        else:
            no_match += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return {"success": False, "reason": f"commit_failed_{e}", "fixed": 0}

    return {
        "success":           True,
        "orphans_found":     len(orphans),
        "fixed":             fixed,
        "no_matching_login": no_match,
        "accounts_mapped":   len(login_to_license),
    }