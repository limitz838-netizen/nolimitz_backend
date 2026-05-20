from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal

from app.models import (
    ClientMT5Account,
    License,
    LiveTrade,
    AISymbol,
    ClientSymbolSetting
)

from app.models import AISymbol
from app.ai.models.ai_market_state import AIMarketState
from app.ai.models.ai_trade_history import AITradeHistory

router = APIRouter(
    prefix="/api/client",
    tags=["Client MT5"]
)


# =========================================================
# SAVE MT5 ACCOUNT
# =========================================================

@router.post("/mt5-account")
def save_mt5_account(
    data: dict,
    db: Session = Depends(get_db)
):

    # =========================
    # FIND LICENSE
    # =========================

    license_key = data.get("license_key")

    license_row = (
        db.query(License)
        .filter(
            License.license_key == license_key
        )
        .first()
    )

    if not license_row:

        return {
            "success": False,
            "message": "Invalid license key"
        }

    # =========================
    # VPS VERIFICATION PENDING
    # =========================

    mt5_info = {

        "broker_name": "Pending VPS Verification",

        "name": f"MT5-{data['login']}",

        "balance": 0,

        "equity": 0
    }

    # =========================
    # CHECK EXISTING ACCOUNT
    # =========================

    existing = (
        db.query(ClientMT5Account)
        .filter(
            ClientMT5Account.license_id == license_row.id
        )
        .first()
    )

    # =========================
    # UPDATE EXISTING
    # =========================

    if existing:

        existing.license_id = license_row.id

        existing.login = data["login"]

        existing.password = data["password"]

        existing.server = data["server"]

        existing.broker_name = mt5_info["broker_name"]

        existing.account_name = mt5_info["name"]

        existing.balance = mt5_info["balance"]

        existing.equity = mt5_info["equity"]

        existing.is_verified = False

        existing.verification_status = "PENDING"

        existing.is_active = True

        db.commit()

        db.refresh(existing)

        return {

            "success": True,

            "message": "MT5 account updated",

            "account": {

                "id": existing.id,

                "name": existing.account_name,

                "broker": existing.broker_name,

                "balance": existing.balance,

                "equity": existing.equity,

                "verified": existing.is_verified
            }
        }

    # =========================
    # CREATE NEW ACCOUNT
    # =========================

    new_account = ClientMT5Account(

        license_id=license_row.id,

        login=data["login"],

        password=data["password"],

        server=data["server"],

        broker_name=mt5_info["broker_name"],

        account_name=mt5_info["name"],

        balance=mt5_info["balance"],

        equity=mt5_info["equity"],

        is_verified=False,

        verification_status="PENDING",

        ai_enabled=False,

        ai_auto_trade=False,

        max_ai_trades=1,

        risk_percent=2.0,

        allow_buy=True,

        allow_sell=True,

        is_active=True
    )

    db.add(new_account)

    db.commit()

    db.refresh(new_account)

    return {

        "success": True,

        "message": "MT5 account connected",

        "account": {

            "id": new_account.id,

            "name": new_account.account_name,

            "broker": new_account.broker_name,

            "balance": new_account.balance,

            "equity": new_account.equity,

            "verified": new_account.is_verified
        }
    }


# =========================================================
# MT5 STATUS
# =========================================================

@router.get("/ai/mt5-status")
def get_mt5_status(
    license_key: str
):

    db = SessionLocal()

    try:

        # =========================
        # FIND LICENSE
        # =========================

        license_row = (
            db.query(License)
            .filter(
                License.license_key == license_key
            )
            .first()
        )

        if not license_row:

            return {
                "connected": False
            }

        # =========================
        # FIND MT5 ACCOUNT
        # =========================

        account = (
            db.query(ClientMT5Account)
            .filter(
                ClientMT5Account.license_id == license_row.id
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

            "name": account.account_name,

            "balance": account.balance,

            "equity": account.equity,

            "verified": account.is_verified,

            "verification_status":
                account.verification_status,

            "last_verified":
                account.last_verified_at
        }

    finally:

        db.close()


# =========================================================
# SAVE AI SETTINGS
# =========================================================

@router.post("/ai/settings")
def save_ai_settings(
    data: dict,
    db: Session = Depends(get_db)
):

    license_key = data.get("license_key")

    license_row = (
        db.query(License)
        .filter(
            License.license_key == license_key
        )
        .first()
    )

    if not license_row:

        return {
            "success": False,
            "message": "Invalid license"
        }

    account = (
        db.query(ClientMT5Account)
        .filter(
            ClientMT5Account.license_id == license_row.id
        )
        .first()
    )

    if not account:

        return {
            "success": False,
            "message": "MT5 account not connected"
        }

    account.lot_size = data.get("lot_size", 0.01)

    account.trades_per_signal = data.get("trades_per_signal", 1)

    account.max_open_trades = data.get("max_open_trades", 3)


    # ============================================
    # SAVE ENABLED SYMBOLS
    # ============================================

    selected_symbols = data.get("symbols", [])

    print("SAVING SYMBOLS:", selected_symbols)

    # REMOVE OLD SETTINGS
    db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == license_row.id
    ).delete()

    # ADD NEW SETTINGS
    for sym in selected_symbols:

        # HANDLE OBJECTS FROM FRONTEND
        if isinstance(sym, dict):

            symbol_name = sym.get("symbol")
            enabled = sym.get("enabled", False)

        else:

            symbol_name = sym
            enabled = True

        # SKIP DISABLED SYMBOLS
        if not enabled:
            continue

        print("LOT SIZE SAVED:", data.get("lot_size"))
        print("TRADES SAVED:", data.get("trades_per_signal"))
        print("MAX TRADES SAVED:", data.get("max_open_trades"))

        existing = (
            db.query(ClientSymbolSetting)
            .filter(
                ClientSymbolSetting.license_id
                == license_row.id,

                ClientSymbolSetting.symbol_name
                == symbol_name
            )
            .first()
        )

        if existing:

            existing.enabled = True

            existing.lot_size = float(
                data.get("lot_size", 0.01)
            )

            existing.trades_per_signal = int(
                data.get("trades_per_signal", 1)
            )

            existing.max_open_trades = int(
                data.get("max_open_trades", 3)
            )

        else:

            db.add(
                ClientSymbolSetting(
                    license_id=license_row.id,
                    symbol_name=symbol_name,
                    enabled=True,

                    lot_size=float(
                        data.get("lot_size", 0.01)
                    ),

                    trades_per_signal=int(
                        data.get("trades_per_signal", 1)
                    ),

                    max_open_trades=int(
                        data.get("max_open_trades", 3)
                    ),
                )
            )

    db.commit()

    return {
        "success": True,
        "message": "AI settings saved"
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
            License.license_key == license_key
        )
        .first()
    )

    if not license_row:

        return {
            "success": False
        }

    account = (
        db.query(ClientMT5Account)
        .filter(
            ClientMT5Account.license_id == license_row.id
        )
        .first()
    )

    if not account:

        return {
            "success": False
        }

    symbol_settings = (
        db.query(ClientSymbolSetting)
        .filter(
            ClientSymbolSetting.license_id == license_row.id
        )
        .all()
    )

    symbols = []

    for s in symbol_settings:
        symbols.append(s.symbol_name)

    first_symbol = symbol_settings[0] if symbol_settings else None

    return {

        "success": True,

        "lot_size":
            first_symbol.lot_size if first_symbol else 0.01,

        "trades_per_signal":
            first_symbol.trades_per_signal if first_symbol else 1,

        "max_open_trades":
            first_symbol.max_open_trades if first_symbol else 3,

        "symbols": symbols
    }


# =========================================================
# LIVE TRADES
# =========================================================

@router.get("/ai/live-trades")
def get_live_trades(
    license_key: str = ""
):

    db = SessionLocal()

    try:

        trades = (
            db.query(AITradeHistory)
            .order_by(
                AITradeHistory.id.desc()
            )
            .all()
        )

        results = []

        for trade in trades:

            results.append({

                "id": trade.id,

                "symbol": trade.symbol,

                "trade_type": trade.signal,

                "lot_size": trade.lot_size,

                "entry_price": trade.entry_price,

                "stop_loss": trade.stop_loss,

                "take_profit": trade.take_profit,

                "profit": trade.profit,

                "status": trade.result,

                "created_at": trade.created_at

            })

        return results

    finally:

        db.close()


# =========================================================
# AI STATUS
# =========================================================

@router.get("/ai/status")
def ai_status():

    db = SessionLocal()

    try:

        pairs = (
            db.query(AISymbol)
            .filter(
                AISymbol.enabled == True
            )
            .count()
        )

        latest = (
            db.query(AIMarketState)
            .order_by(
                AIMarketState.id.desc()
            )
            .first()
        )

        return {

            "ai_active": True,

            "pairs_tracked": pairs,

            "last_scan":
                latest.updated_at if latest else None
        }

    finally:

        db.close()


# =========================================================
# AI SYMBOLS
# =========================================================

@router.get("/ai/symbols")
def ai_symbols():

    db = SessionLocal()

    try:

        symbols = db.query(AISymbol).all()

        return [

            {
                "symbol": s.symbol,
                "enabled": s.enabled
            }

            for s in symbols
        ]

    finally:

        db.close()


@router.post("/ai/symbols")
def save_ai_symbols(
    data: dict,
    db: Session = Depends(get_db)
):

    license_key = data.get("license_key")

    symbols = data.get("symbols", [])

    license_row = (
        db.query(License)
        .filter(
            License.license_key == license_key
        )
        .first()
    )

    if not license_row:

        return {
            "success": False,
            "message": "Invalid license"
        }

    # DELETE OLD
    db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == license_row.id
    ).delete()

    # SAVE NEW
    for sym in symbols:

        # HANDLE OBJECTS FROM FRONTEND
        if isinstance(sym, dict):

            symbol_name = sym.get("symbol")
            enabled = sym.get("enabled", False)

        else:

            symbol_name = sym
            enabled = True

        # SKIP DISABLED SYMBOLS
        if not enabled:
            continue

        db.add(
            ClientSymbolSetting(
                license_id=license_row.id,
                symbol_name=symbol_name,
                enabled=True,
                trade_direction="both",
                lot_size=data.get("lot_size", 0.01),
                trades_per_signal=data.get("trades_per_signal", 1),
                max_open_trades=data.get("max_open_trades", 3)
            )
        )

    db.commit()

    return {
        "success": True,
        "symbols": symbols
    }

# =========================================================
# MARKET DATA
# =========================================================

@router.get("/ai/market-data")
def market_data(
    symbol: str = "XAUUSD"
):

    db = SessionLocal()

    try:

        state = (
            db.query(AIMarketState)
            .filter(
                AIMarketState.symbol == symbol
            )
            .first()
        )

        if not state:

            return {
                "success": False
            }

        return {

            "success": True,

            "symbol": state.symbol,

            "signal": state.signal,

            "trend": state.trend,

            "confidence": state.confidence,

            "entry": state.entry,

            "stop_loss": state.stop_loss,

            "take_profit": state.take_profit,

            "analysis": state.analysis,

            "updated_at": state.updated_at
        }

    finally:

        db.close()        


@router.get("/ai/trade-history")
def get_trade_history(
    license_key: str = ""
):

    db = SessionLocal()

    try:

        trades = (
            db.query(AITradeHistory)
            .order_by(
                AITradeHistory.id.desc()
            )
            .all()
        )

        results = []

        wins = 0
        losses = 0
        total_profit = 0

        for trade in trades:

            profit = trade.profit or 0

            total_profit += profit

            if profit > 0:
                wins += 1
            elif profit < 0:
                losses += 1

            results.append({

                "symbol": trade.symbol,

                "trade_type": trade.signal,

                "profit": profit,

                "status": trade.result,

                "created_at": trade.created_at

            })

        total = wins + losses

        win_rate = 0

        if total > 0:

            win_rate = round(
                (wins / total) * 100,
                1
            )

        return {

            "total_trades": len(trades),

            "wins": wins,

            "losses": losses,

            "win_rate": win_rate,

            "net_profit": round(total_profit, 2),

            "trades": results

        }

    finally:

        db.close()