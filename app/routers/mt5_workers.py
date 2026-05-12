from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ClientMT5Account

router = APIRouter(
    prefix="/worker",
    tags=["MT5 Workers"]
)


def utc_now():
    return datetime.now(timezone.utc)


@router.get("/pending-mt5")
def get_pending_mt5_accounts(db: Session = Depends(get_db)):

    rows = db.query(ClientMT5Account).filter(
        ClientMT5Account.is_verified == False
    ).all()

    results = []

    for row in rows:
        results.append({
            "id": row.id,
            "license_id": row.license_id,
            "mt_login": row.mt_login,
            "mt_password": row.mt_password,
            "mt_server": row.mt_server,
        })

    return {
        "count": len(results),
        "accounts": results,
    }


@router.post("/update-mt5-status")
def update_mt5_status(
    payload: dict,
    db: Session = Depends(get_db)
):

    row = db.query(ClientMT5Account).filter(
        ClientMT5Account.id == payload.get("id")
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="MT5 account not found")

    success = payload.get("success", False)

    row.last_verified_at = utc_now()

    if success:
        row.is_verified = True
        row.is_active = True
        row.verification_error = None

        row.account_name = payload.get("account_name")
        row.broker_name = payload.get("broker_name")
        row.balance = payload.get("balance")
        row.equity = payload.get("equity")

    else:
        row.is_verified = False
        row.is_active = False
        row.verification_error = payload.get(
            "error",
            "Verification failed"
        )

    db.commit()
    db.refresh(row)

    return {
        "message": "MT5 status updated",
        "verified": row.is_verified,
    }