from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal

from app.models import (
    ClientMT5Account,
    License,
    LiveTrade,
)

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

    return {

        "success": True,

        "lot_size":
            account.lot_size,

        "trades_per_signal":
            account.trades_per_signal,

        "max_open_trades":
            account.max_open_trades
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
            db.query(LiveTrade)
            .filter(
                LiveTrade.status == "OPEN"
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

                "profit": trade.profit,

                "status": trade.status,

                "mt5_ticket": trade.mt5_ticket,

                "created_at": trade.created_at
            })

        return results

    finally:

        db.close()
