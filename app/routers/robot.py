from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth import decode_access_token
from app.database import get_db
from app.models import Admin, ExpertAdvisor, EASymbol, License, RobotTrade
from app.schemas import RobotTradeCreateRequest, RobotTradeItem, ClientSignalsRequest

router = APIRouter(prefix="/robot", tags=["Robot"])


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


@router.post("/create", response_model=RobotTradeItem)
def create_robot_trade(
    payload: RobotTradeCreateRequest,
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

    if ea.mode_type != "robot":
        raise HTTPException(status_code=400, detail="Only robot mode EAs can create robot trades")

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

    trade = RobotTrade(
        admin_id=current_admin.id,
        ea_id=ea.id,
        symbol=symbol,
        action=action,
        lot_size=payload.lot_size,
        sl=payload.sl,
        tp=payload.tp,
        comment=payload.comment,
        status="pending",
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)

    return RobotTradeItem(
        id=trade.id,
        ea_id=trade.ea_id,
        symbol=trade.symbol,
        action=trade.action,
        lot_size=trade.lot_size,
        sl=trade.sl,
        tp=trade.tp,
        comment=trade.comment,
        status=trade.status,
        created_at=trade.created_at,
    )


@router.get("/my", response_model=List[RobotTradeItem])
def list_my_robot_trades(
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    trades = db.query(RobotTrade).filter(
        RobotTrade.admin_id == current_admin.id
    ).order_by(RobotTrade.id.desc()).all()

    return [
        RobotTradeItem(
            id=t.id,
            ea_id=t.ea_id,
            symbol=t.symbol,
            action=t.action,
            lot_size=t.lot_size,
            sl=t.sl,
            tp=t.tp,
            comment=t.comment,
            status=t.status,
            created_at=t.created_at,
        )
        for t in trades
    ]


@router.post("/client-feed", response_model=List[RobotTradeItem])
def get_client_robot_trades(
    payload: ClientSignalsRequest,
    db: Session = Depends(get_db),
):
    license = db.query(License).filter(
        License.license_key == payload.license_key,
        License.is_active == True,
    ).first()

    if not license:
        raise HTTPException(status_code=404, detail="Invalid or inactive license")

    trades = db.query(RobotTrade).filter(
        RobotTrade.ea_id == license.ea_id,
        RobotTrade.status == "pending",
    ).order_by(RobotTrade.id.desc()).all()

    return [
        RobotTradeItem(
            id=t.id,
            ea_id=t.ea_id,
            symbol=t.symbol,
            action=t.action,
            lot_size=t.lot_size,
            sl=t.sl,
            tp=t.tp,
            comment=t.comment,
            status=t.status,
            created_at=t.created_at,
        )
        for t in trades
    ]