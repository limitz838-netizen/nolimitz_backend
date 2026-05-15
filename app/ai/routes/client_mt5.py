from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ClientMT5Account

router = APIRouter(
    prefix="/api/client",
    tags=["Client MT5"]
)

@router.post("/mt5-account")
def save_mt5_account(
    data: dict,
    db: Session = Depends(get_db)
):
    
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
            ClientMT5Account.login == data["login"]
        )
        .first()
    )

    # =========================
    # UPDATE EXISTING
    # =========================

    if existing:

        existing.login = data["login"]
        existing.password = data["password"]
        existing.server = data["server"]

        existing.broker_name = mt5_info["broker_name"]

        existing.account_name = mt5_info["name"]

        existing.balance = mt5_info["balance"]

        existing.equity = mt5_info["equity"]

        existing.is_verified = True

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

        login=data["login"],

        password=data["password"],

        server=data["server"],

        broker_name=mt5_info["broker_name"],

        account_name=mt5_info["name"],

        balance=mt5_info["balance"],

        equity=mt5_info["equity"],

        is_verified=True,

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