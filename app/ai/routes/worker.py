from fastapi import APIRouter
from app.database import SessionLocal
from app.models import ClientMT5Account

router = APIRouter(
    prefix="/api/worker",
    tags=["Worker"]
)

@router.get("/active-accounts")
def get_active_accounts():

    db = SessionLocal()

    accounts = (
        db.query(ClientMT5Account)
        .filter(
            ClientMT5Account.is_active == True,
            ClientMT5Account.ai_auto_trade == True
        )
        .all()
    )

    result = []

    for acc in accounts:

        result.append({

            "id": acc.id,

            "login": acc.login,

            "password": acc.password,

            "server": acc.server,

            "risk_percent": acc.risk_percent,

            "max_ai_trades": acc.max_ai_trades
        })

    db.close()

    return result