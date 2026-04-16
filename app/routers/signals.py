from typing import List

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.auth import decode_access_token
from app.database import get_db
from app.models import Admin, ExpertAdvisor, EASymbol, License, Signal
from app.schemas import SignalPushRequest, SignalItem, ClientSignalsRequest

router = APIRouter(prefix="/signals", tags=["Signals"])


def get_current_admin(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> Admin:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")

    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    admin_id = payload.get("admin_id")
    admin = db.query(Admin).filter(Admin.id == admin_id).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    if not admin.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    if admin.role != "super_admin" and not admin.is_approved:
        raise HTTPException(status_code=403, detail="Your account is pending approval")

    return admin


@router.post("/push", response_model=SignalItem)
def push_signal(
    payload: SignalPushRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    ea = db.query(ExpertAdvisor).filter(
        ExpertAdvisor.id == payload.ea_id,
        ExpertAdvisor.admin_id == current_admin.id,
        ExpertAdvisor.is_active == True,
    ).first()

    if not ea:
        raise HTTPException(status_code=404, detail="EA not found or inactive")

    symbol = payload.symbol.upper().strip()
    action = payload.action.lower().strip()

    if action not in ["buy", "sell"]:
        raise HTTPException(status_code=400, detail="action must be buy or sell")

    allowed_symbol = db.query(EASymbol).filter(
        EASymbol.ea_id == ea.id,
        EASymbol.symbol_name == symbol,
        EASymbol.enabled == True,
    ).first()

    if not allowed_symbol:
        raise HTTPException(status_code=403, detail="Symbol is not allowed for this EA")

    signal = Signal(
        admin_id=current_admin.id,
        ea_id=ea.id,
        symbol=symbol,
        action=action,
        sl=payload.sl,
        tp=payload.tp,
        comment=payload.comment,
        status="active",
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)

    return SignalItem(
        id=signal.id,
        ea_id=signal.ea_id,
        symbol=signal.symbol,
        action=signal.action,
        sl=signal.sl,
        tp=signal.tp,
        comment=signal.comment,
        status=signal.status,
        created_at=signal.created_at,
    )


@router.get("/my", response_model=List[SignalItem])
def list_my_signals(
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    signals = db.query(Signal).filter(
        Signal.admin_id == current_admin.id
    ).order_by(Signal.id.desc()).all()

    return [
        SignalItem(
            id=s.id,
            ea_id=s.ea_id,
            symbol=s.symbol,
            action=s.action,
            sl=s.sl,
            tp=s.tp,
            comment=s.comment,
            status=s.status,
            created_at=s.created_at,
        )
        for s in signals
    ]


@router.post("/client-feed", response_model=List[SignalItem])
def get_client_signals(
    payload: ClientSignalsRequest,
    db: Session = Depends(get_db),
):
    license = db.query(License).filter(
        License.license_key == payload.license_key,
        License.is_active == True,
    ).first()

    if not license:
        raise HTTPException(status_code=404, detail="Invalid or inactive license")

    signals = db.query(Signal).filter(
        Signal.ea_id == license.ea_id,
        Signal.status == "active",
    ).order_by(Signal.id.desc()).all()

    return [
        SignalItem(
            id=s.id,
            ea_id=s.ea_id,
            symbol=s.symbol,
            action=s.action,
            sl=s.sl,
            tp=s.tp,
            comment=s.comment,
            status=s.status,
            created_at=s.created_at,
        )
        for s in signals
    ]