from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone

from app.database import get_db
from app.models import (
    ClientMT5Account, License, LiveTrade,
    ClientSymbolSetting, AISymbol
)
from app.ai.models.ai_trade_history import AITradeHistory
from app.ai.models.ai_market_state import AIMarketState
from app.config.brokers import BROKERS

router = APIRouter(
    prefix="/api/client",
    tags=["Client MT5 & AI"]
)

# =========================
# PYDANTIC MODELS
# =========================
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

# =========================
# BROKERS
# =========================
@router.get("/ai/brokers")
def get_brokers():
    return {
        "success": True,
        "brokers": BROKERS
    }

# =========================
# MT5 ACCOUNT
# =========================
@router.post("/mt5-account")
def save_mt5_account(data: MT5AccountCreate, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(License.license_key == data.license_key).first()
    if not license_row:
        raise HTTPException(status_code=400, detail="Invalid license key")

    risk = data.risk_level.lower()
    if risk not in ["low", "medium", "aggressive"]:
        risk = "medium"

    existing = db.query(ClientMT5Account).filter(ClientMT5Account.license_id == license_row.id).first()

    if existing:
        existing.login = data.login
        existing.password = data.password
        existing.server = data.server
        existing.risk_level = risk
        existing.is_active = True
        db.commit()
        return {"success": True, "message": "MT5 account updated"}

    new_account = ClientMT5Account(
        license_id=license_row.id,
        login=data.login,
        password=data.password,
        server=data.server,
        risk_level=risk,
        is_active=True,
        ai_auto_trade=False
    )
    db.add(new_account)
    db.commit()
    return {"success": True, "message": "MT5 account connected successfully"}

# =========================
# MT5 STATUS
# =========================
@router.get("/ai/mt5-status")
def get_mt5_status(license_key: str, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(License.license_key == license_key).first()
    if not license_row:
        return {"connected": False}

    account = db.query(ClientMT5Account).filter(ClientMT5Account.license_id == license_row.id).first()
    if not account:
        return {"connected": False}

    return {
        "connected": True,
        "login": account.login,
        "account_name": account.account_name,
        "broker": account.broker_name,
        "server": account.server,
        "balance": float(account.balance or 0),
        "equity": float(account.equity or 0),
        "verified": account.is_verified,
        "risk_level": account.risk_level or "medium",
        "last_verified_at": account.last_verified_at.isoformat() if account.last_verified_at else None
    }

# =========================
# AI SETTINGS
# =========================
@router.post("/ai/settings")
def save_ai_settings(data: AISettingsUpdate, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(License.license_key == data.license_key).first()
    if not license_row:
        raise HTTPException(status_code=400, detail="Invalid license key")

    db.query(ClientSymbolSetting).filter(ClientSymbolSetting.license_id == license_row.id).delete()

    for symbol in data.symbols[:20]:  # Max 20 symbols
        db.add(ClientSymbolSetting(
            license_id=license_row.id,
            symbol_name=symbol.upper(),
            enabled=True,
            trade_direction="both"
        ))

    db.commit()
    return {"success": True, "message": "AI settings saved successfully"}

@router.get("/ai/settings")
def get_ai_settings(license_key: str, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(License.license_key == license_key).first()
    if not license_row:
        raise HTTPException(status_code=404, detail="License not found")

    settings = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == license_row.id,
        ClientSymbolSetting.enabled == True
    ).all()

    return {
        "success": True,
        "symbols": [s.symbol_name for s in settings]
    }

# =========================
# LIVE TRADES
# =========================
@router.get("/ai/live-trades")
def get_live_trades(license_key: str, db: Session = Depends(get_db)):
    trades = db.query(LiveTrade).filter(
        LiveTrade.license_key == license_key,
        LiveTrade.status == "OPEN"
    ).order_by(LiveTrade.id.desc()).all()

    return [{
        "id": t.id,
        "symbol": t.symbol,
        "trade_type": t.trade_type,
        "lot_size": t.lot_size,
        "entry_price": t.entry_price,
        "stop_loss": t.stop_loss,
        "take_profit": t.take_profit,
        "profit": round(t.profit or 0, 2),
        "status": t.status,
        "mt5_ticket": t.mt5_ticket,
        "opened_at": t.opened_at.isoformat() if t.opened_at else None
    } for t in trades]

# =========================
# TRADE HISTORY (with pagination)
# =========================
@router.get("/ai/trade-history")
def get_trade_history(
    license_key: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    license_row = db.query(License).filter(License.license_key == license_key).first()
    if not license_row:
        return {"total_trades": 0, "trades": [], "page": page}

    offset = (page - 1) * limit
    trades = db.query(AITradeHistory).filter(
        AITradeHistory.license_id == license_row.id
    ).order_by(AITradeHistory.id.desc()).offset(offset).limit(limit).all()

    total = db.query(AITradeHistory).filter(AITradeHistory.license_id == license_row.id).count()

    return {
        "total_trades": total,
        "page": page,
        "limit": limit,
        "trades": [{
            "symbol": t.symbol,
            "trade_type": t.signal,
            "profit": round(t.profit or 0, 2),
            "status": t.result,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "closed_at": t.closed_at.isoformat() if t.closed_at else None
        } for t in trades]
    }

# =========================
# SIGNALS PRO
# =========================
@router.get("/ai/signals-pro")
def get_signals_pro(license_key: str, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(License.license_key == license_key).first()
    if not license_row:
        raise HTTPException(status_code=404, detail="License not found")

    trades = db.query(AITradeHistory).filter(AITradeHistory.license_id == license_row.id).all()
    total = len(trades)
    wins = sum(1 for t in trades if (t.profit or 0) > 0)
    losses = sum(1 for t in trades if (t.profit or 0) < 0)
    net = round(sum(t.profit or 0 for t in trades), 2)
    win_rate = round((wins / total) * 100, 1) if total > 0 else 0

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_profit": net
    }

# =========================
# MARKET DATA
# =========================
@router.get("/ai/market-data")
def get_market_data(symbol: str, db: Session = Depends(get_db)):
    market = db.query(AIMarketState).filter(AIMarketState.symbol == symbol.upper()).first()
    if not market:
        return {"success": False, "symbol": symbol, "signal": "NONE"}

    return {
        "success": True,
        "symbol": market.symbol,
        "direction": market.trend,
        "signal": market.signal,
        "entry_price": market.entry,
        "strength": market.confidence,
        "analysis": market.analysis,
        "stop_loss": market.stop_loss,
        "take_profit": market.take_profit,
        "updated_at": market.updated_at.isoformat() if market.updated_at else None
    }

@router.get("/ai/symbols")
def get_ai_symbols(db: Session = Depends(get_db)):
    markets = db.query(AIMarketState).limit(50).all()
    return {
        "success": True,
        "symbols": [{
            "symbol": m.symbol,
            "direction": m.trend,
            "signal": m.signal,
            "strength": m.confidence,
            "entry_price": m.entry,
            "stop_loss": m.stop_loss,
            "take_profit": m.take_profit,
            "analysis": m.analysis,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None
        } for m in markets]
    }

@router.post("/ai/symbols")
def save_ai_symbols(data: SaveSymbolsRequest, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(License.license_key == data.license_key).first()
    if not license_row:
        raise HTTPException(status_code=404, detail="License not found")

    db.query(ClientSymbolSetting).filter(ClientSymbolSetting.license_id == license_row.id).delete()

    for sym in data.symbols[:20]:
        db.add(ClientSymbolSetting(
            license_id=license_row.id,
            symbol_name=sym.upper(),
            enabled=True,
            trade_direction="both"
        ))

    db.commit()
    return {"success": True, "message": "Symbols saved successfully"}

# =========================
# AI STATUS
# =========================
@router.get("/ai/status")
def ai_status(db: Session = Depends(get_db)):
    count = db.query(AISymbol).filter(AISymbol.enabled == True).count()
    return {
        "ai_active": True,
        "pairs_tracked": count
    }