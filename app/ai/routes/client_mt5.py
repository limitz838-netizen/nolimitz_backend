from fastapi import APIRouter, Depends, HTTPException

from sqlalchemy.orm import Session

from pydantic import BaseModel

from typing import List

from app.database import get_db

from app.models import (
    ClientMT5Account,
    License,
    LiveTrade,
    ClientSymbolSetting,
    AISymbol
)

from app.ai.models.ai_trade_history import (
    AITradeHistory
)
from app.ai.models.ai_market_state import AIMarketState

router = APIRouter(
    prefix="/api/client",
    tags=["Client MT5 & AI"]
)


# =========================================================
# MODELS
# =========================================================

class MT5AccountCreate(BaseModel):

    license_key: str

    login: str

    password: str

    server: str

    risk_level: str = "medium"


class AISettingsUpdate(BaseModel):

    license_key: str

    symbols: List[str] = []


# =========================================================
# SAVE MT5 ACCOUNT
# =========================================================

@router.post("/mt5-account")
def save_mt5_account(
    data: MT5AccountCreate,
    db: Session = Depends(get_db)
):

    license_row = (
        db.query(License)
        .filter(
            License.license_key
            == data.license_key
        )
        .first()
    )

    if not license_row:

        raise HTTPException(
            status_code=400,
            detail="Invalid license key"
        )

    existing = (
        db.query(ClientMT5Account)
        .filter(
            ClientMT5Account.license_id
            == license_row.id
        )
        .first()
    )

    if existing:

        existing.login = data.login

        existing.password = data.password

        existing.server = data.server

        existing.risk_level = (
            data.risk_level.lower()
        )

        existing.is_active = True

        db.commit()

        db.refresh(existing)

        return {
            "success": True,
            "message": "MT5 updated"
        }

    new_account = ClientMT5Account(

        license_id=license_row.id,

        login=data.login,

        password=data.password,

        server=data.server,

        risk_level=data.risk_level.lower(),

        is_active=True,

        ai_auto_trade=False
    )

    db.add(new_account)

    db.commit()

    db.refresh(new_account)

    return {
        "success": True,
        "message": "MT5 connected"
    }


# =========================================================
# MT5 STATUS
# =========================================================

@router.get("/ai/mt5-status")
def get_mt5_status(
    license_key: str,
    db: Session = Depends(get_db)
):

    license_row = (
        db.query(License)
        .filter(
            License.license_key
            == license_key
        )
        .first()
    )

    if not license_row:

        return {
            "connected": False
        }

    account = (
        db.query(ClientMT5Account)
        .filter(
            ClientMT5Account.license_id
            == license_row.id
        )
        .first()
    )

    if not account:

        return {
            "connected": False
        }

    return {

        "connected": True,

        "login": account.login,

        "broker": account.broker_name,

        "server": account.server,

        "balance": account.balance or 0,

        "equity": account.equity or 0,

        "verified": account.is_verified,

        "risk_level": (
            account.risk_level
            or "medium"
        )
    }


# =========================================================
# AI SETTINGS
# =========================================================

@router.post("/ai/settings")
def save_ai_settings(
    data: AISettingsUpdate,
    db: Session = Depends(get_db)
):

    license_row = (
        db.query(License)
        .filter(
            License.license_key
            == data.license_key
        )
        .first()
    )

    if not license_row:

        raise HTTPException(
            status_code=400,
            detail="Invalid license key"
        )

    db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id
        == license_row.id
    ).delete()

    for symbol in data.symbols:

        setting = ClientSymbolSetting(

            license_id=license_row.id,

            symbol_name=symbol.upper(),

            enabled=True,

            trade_direction="both"
        )

        db.add(setting)

    db.commit()

    return {
        "success": True,
        "message": "Symbols updated"
    }


# =========================================================
# GET AI SETTINGS
# =========================================================

@router.get("/ai/settings")
def get_ai_settings(
    license_key: str,
    db: Session = Depends(get_db)
):

    license_row = (
        db.query(License)
        .filter(
            License.license_key
            == license_key
        )
        .first()
    )

    if not license_row:

        raise HTTPException(
            status_code=404,
            detail="License not found"
        )

    settings = (
        db.query(ClientSymbolSetting)
        .filter(
            ClientSymbolSetting.license_id
            == license_row.id,

            ClientSymbolSetting.enabled
            == True
        )
        .all()
    )

    return {

        "success": True,

        "symbols": [
            s.symbol_name
            for s in settings
        ]
    }


# =========================================================
# LIVE TRADES
# =========================================================

@router.get("/ai/live-trades")
def get_live_trades(
    license_key: str,
    db: Session = Depends(get_db)
):

    trades = (
        db.query(LiveTrade)
        .filter(
            LiveTrade.license_key
            == license_key,

            LiveTrade.status
            == "OPEN"
        )
        .order_by(
            LiveTrade.id.desc()
        )
        .all()
    )

    results = []

    for trade in trades:

        results.append({

            "id": trade.id,

            "symbol": trade.symbol,

            "trade_type": trade.trade_type,

            "lot_size": trade.lot_size,

            "entry_price": trade.entry_price,

            "stop_loss": trade.stop_loss,

            "take_profit": trade.take_profit,

            "profit": round(
                trade.profit or 0,
                2
            ),

            "status": trade.status,

            "mt5_ticket": trade.mt5_ticket,

            "opened_at": trade.opened_at
        })

    return results


# =========================================================
# TRADE HISTORY
# =========================================================

@router.get("/ai/trade-history")
def get_trade_history(
    license_key: str,
    db: Session = Depends(get_db)
):

    license_row = (
        db.query(License)
        .filter(
            License.license_key
            == license_key
        )
        .first()
    )

    if not license_row:

        return {
            "total_trades": 0,
            "trades": []
        }

    trades = (
        db.query(AITradeHistory)
        .filter(
            AITradeHistory.license_id
            == license_row.id
        )
        .order_by(
            AITradeHistory.id.desc()
        )
        .all()
    )

    results = []

    for trade in trades:

        results.append({

            "symbol": trade.symbol,

            "trade_type": trade.signal,

            "profit": round(
                trade.profit or 0,
                2
            ),

            "status": trade.result,

            "created_at": trade.created_at,

            "closed_at": trade.closed_at
        })

    return {

        "total_trades": len(results),

        "trades": results
    }


# =========================================================
# SIGNALS PRO
# =========================================================

@router.get("/ai/signals-pro")
def get_signals_pro(
    license_key: str,
    db: Session = Depends(get_db)
):

    license_row = (
        db.query(License)
        .filter(
            License.license_key
            == license_key
        )
        .first()
    )

    if not license_row:

        raise HTTPException(
            status_code=404,
            detail="License not found"
        )

    trades = (
        db.query(AITradeHistory)
        .filter(
            AITradeHistory.license_id
            == license_row.id
        )
        .all()
    )

    total_trades = len(trades)

    wins = sum(
        1 for t in trades
        if (t.profit or 0) > 0
    )

    losses = sum(
        1 for t in trades
        if (t.profit or 0) < 0
    )

    net_profit = round(
        sum(t.profit or 0 for t in trades),
        2
    )

    win_rate = 0

    if total_trades > 0:

        win_rate = round(
            (wins / total_trades) * 100,
            1
        )

    return {

        "total_trades": total_trades,

        "wins": wins,

        "losses": losses,

        "win_rate": win_rate,

        "net_profit": net_profit
    }


# =========================================================
# AI STATUS
# =========================================================

@router.get("/ai/status")
def ai_status(
    db: Session = Depends(get_db)
):

    pairs = (
        db.query(AISymbol)
        .filter(
            AISymbol.enabled == True
        )
        .count()
    )

    return {

        "ai_active": True,

        "pairs_tracked": pairs
    }

@router.get("/ai/market-data")
def get_market_data(
    symbol: str,
    db: Session = Depends(get_db)
):
    market = db.query(AIMarketState).filter(
        AIMarketState.symbol == symbol.upper()
    ).first()

    if not market:
        return {
            "success": False,
            "symbol": symbol,
            "direction": "NEUTRAL",
            "entry_price": 0,
            "strength": 0,
            "updated_at": None
        }

    return {
        "success": True,
        "symbol": market.symbol,
        "direction": market.trend,
        "entry_price": market.entry_price,
        "strength": market.strength,
        "updated_at": market.updated_at.isoformat() if market.updated_at else None
    }

@router.get("/ai/symbols")
def get_ai_symbols(
    db: Session = Depends(get_db)
):
    markets = db.query(AIMarketState).filter(
        AIMarketState.active == True
    ).all()

    return {
        "success": True,
        "symbols": [
            {
                "symbol": m.symbol,
                "direction": m.trend,
                "strength": m.strength,
                "entry_price": m.entry_price,
                "updated_at": m.updated_at.isoformat() if m.updated_at else None
            }
            for m in markets
        ]
    }