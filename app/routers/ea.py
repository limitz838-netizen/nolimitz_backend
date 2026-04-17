import random
import string
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Admin, ExpertAdvisor, EASymbol, AdminEALink
from app.schemas import (
    EACreateRequest,
    EAUpdateRequest,
    EASymbolsRequest,
    EAItem,
    EASymbolItem,
    BasicMessageResponse,
    EALinkRequest,
    EALinkItem,
)
from app.auth import decode_access_token

router = APIRouter(prefix="/eas", tags=["EAs"])


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


def generate_ea_code(db: Session, admin_code: int) -> str:
    while True:
        random_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        ea_code = f"EA-NL-{admin_code}-{random_part}"

        existing = db.query(ExpertAdvisor).filter(ExpertAdvisor.ea_code == ea_code).first()
        if not existing:
            return ea_code


@router.post("/", response_model=EAItem)
def create_ea(
    payload: EACreateRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
  

    ea = ExpertAdvisor(
        admin_id=current_admin.id,
        name=payload.name,
        code_name=payload.code_name,
        ea_code=generate_ea_code(db, current_admin.admin_code),
        version=payload.version,
        description=payload.description,
        mode_type="both",
        is_shareable=payload.is_shareable,
        is_active=True,
    )
    db.add(ea)
    db.commit()
    db.refresh(ea)

    return EAItem(
        id=ea.id,
        name=ea.name,
        code_name=ea.code_name,
        ea_code=ea.ea_code,
        version=ea.version,
        description=ea.description,
        is_shareable=ea.is_shareable,
        is_active=ea.is_active,
        symbols=[],
    )


@router.get("/", response_model=List[EAItem])
def list_my_eas(
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    eas = (
        db.query(ExpertAdvisor)
        .filter(ExpertAdvisor.admin_id == current_admin.id)
        .order_by(ExpertAdvisor.id.desc())
        .all()
    )

    result = []
    for ea in eas:
        result.append(
            EAItem(
                id=ea.id,
                name=ea.name,
                code_name=ea.code_name,
                ea_code=ea.ea_code,
                version=ea.version,
                description=ea.description,
                is_shareable=ea.is_shareable,
                is_active=ea.is_active,
                symbols=[
                    EASymbolItem(
                        id=s.id,
                        symbol_name=s.symbol_name,
                        enabled=s.enabled,
                    )
                    for s in ea.symbols
                ],
            )
        )
    return result


@router.get("/{ea_id}", response_model=EAItem)
def get_ea(
    ea_id: int,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    ea = (
        db.query(ExpertAdvisor)
        .filter(ExpertAdvisor.id == ea_id, ExpertAdvisor.admin_id == current_admin.id)
        .first()
    )
    if not ea:
        raise HTTPException(status_code=404, detail="EA not found")

    return EAItem(
        id=ea.id,
        name=ea.name,
        code_name=ea.code_name,
        ea_code=ea.ea_code,
        version=ea.version,
        description=ea.description,
        is_shareable=ea.is_shareable,
        is_active=ea.is_active,
        symbols=[
            EASymbolItem(
                id=s.id,
                symbol_name=s.symbol_name,
                enabled=s.enabled,
            )
            for s in ea.symbols
        ],
    )


@router.put("/{ea_id}", response_model=EAItem)
def update_ea(
    ea_id: int,
    payload: EAUpdateRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    ea = (
        db.query(ExpertAdvisor)
        .filter(ExpertAdvisor.id == ea_id, ExpertAdvisor.admin_id == current_admin.id)
        .first()
    )
    if not ea:
        raise HTTPException(status_code=404, detail="EA not found")


    if payload.name is not None:
        ea.name = payload.name
    if payload.code_name is not None:
        ea.code_name = payload.code_name
    if payload.version is not None:
        ea.version = payload.version
    if payload.description is not None:
        ea.description = payload.description
    if payload.is_shareable is not None:
        ea.is_shareable = payload.is_shareable
    if payload.is_active is not None:
        ea.is_active = payload.is_active

    db.commit()
    db.refresh(ea)

    return EAItem(
        id=ea.id,
        name=ea.name,
        code_name=ea.code_name,
        ea_code=ea.ea_code,
        version=ea.version,
        description=ea.description,
        is_shareable=ea.is_shareable,
        is_active=ea.is_active,
        symbols=[
            EASymbolItem(
                id=s.id,
                symbol_name=s.symbol_name,
                enabled=s.enabled,
            )
            for s in ea.symbols
        ],
    )


@router.post("/{ea_id}/symbols", response_model=EAItem)
def save_ea_symbols(
    ea_id: int,
    payload: EASymbolsRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    ea = (
        db.query(ExpertAdvisor)
        .filter(ExpertAdvisor.id == ea_id, ExpertAdvisor.admin_id == current_admin.id)
        .first()
    )
    if not ea:
        raise HTTPException(status_code=404, detail="EA not found")

    db.query(EASymbol).filter(EASymbol.ea_id == ea.id).delete()

    clean_symbols = []
    seen = set()
    for symbol in payload.symbols:
        s = symbol.strip().upper()
        if s and s not in seen:
            seen.add(s)
            clean_symbols.append(s)

    for symbol in clean_symbols:
        db.add(EASymbol(ea_id=ea.id, symbol_name=symbol, enabled=True))

    db.commit()
    db.refresh(ea)

    ea = (
        db.query(ExpertAdvisor)
        .filter(ExpertAdvisor.id == ea_id, ExpertAdvisor.admin_id == current_admin.id)
        .first()
    )

    return EAItem(
        id=ea.id,
        name=ea.name,
        code_name=ea.code_name,
        ea_code=ea.ea_code,
        version=ea.version,
        description=ea.description,
        is_shareable=ea.is_shareable,
        is_active=ea.is_active,
        symbols=[
            EASymbolItem(
                id=s.id,
                symbol_name=s.symbol_name,
                enabled=s.enabled,
            )
            for s in ea.symbols
        ],
    )


@router.post("/{ea_id}/activate", response_model=BasicMessageResponse)
def activate_ea(
    ea_id: int,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    ea = (
        db.query(ExpertAdvisor)
        .filter(ExpertAdvisor.id == ea_id, ExpertAdvisor.admin_id == current_admin.id)
        .first()
    )
    if not ea:
        raise HTTPException(status_code=404, detail="EA not found")

    ea.is_active = True
    db.commit()

    return BasicMessageResponse(message="EA activated successfully")


@router.post("/{ea_id}/deactivate", response_model=BasicMessageResponse)
def deactivate_ea(
    ea_id: int,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    ea = (
        db.query(ExpertAdvisor)
        .filter(ExpertAdvisor.id == ea_id, ExpertAdvisor.admin_id == current_admin.id)
        .first()
    )
    if not ea:
        raise HTTPException(status_code=404, detail="EA not found")

    ea.is_active = False
    db.commit()

    return BasicMessageResponse(message="EA deactivated successfully")


@router.post("/link/by-code", response_model=EALinkItem)
def link_ea_by_code(
    payload: EALinkRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    source_ea = db.query(ExpertAdvisor).filter(
        ExpertAdvisor.ea_code == payload.ea_code
    ).first()

    if not source_ea:
        raise HTTPException(status_code=404, detail="EA code not found")

    if not source_ea.is_shareable:
        raise HTTPException(status_code=403, detail="This EA is not shareable")

    if source_ea.admin_id == current_admin.id:
        raise HTTPException(status_code=400, detail="You cannot link your own EA")

    existing = db.query(AdminEALink).filter(
        AdminEALink.source_ea_id == source_ea.id,
        AdminEALink.target_admin_id == current_admin.id,
    ).first()

    if existing:
        return EALinkItem(
            id=existing.id,
            source_admin_id=existing.source_admin_id,
            target_admin_id=existing.target_admin_id,
            source_ea_id=existing.source_ea_id,
            source_ea_code=existing.source_ea_code,
            status=existing.status,
            is_active=existing.is_active,
        )

    link = AdminEALink(
        source_admin_id=source_ea.admin_id,
        target_admin_id=current_admin.id,
        source_ea_id=source_ea.id,
        source_ea_code=source_ea.ea_code,
        status="approved",
        is_active=True,
    )
    db.add(link)
    db.commit()
    db.refresh(link)

    return EALinkItem(
        id=link.id,
        source_admin_id=link.source_admin_id,
        target_admin_id=link.target_admin_id,
        source_ea_id=link.source_ea_id,
        source_ea_code=link.source_ea_code,
        status=link.status,
        is_active=link.is_active,
    )


@router.get("/links/mine", response_model=List[EALinkItem])
def list_my_linked_eas(
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    links = db.query(AdminEALink).filter(
        AdminEALink.target_admin_id == current_admin.id
    ).order_by(AdminEALink.id.desc()).all()

    return [
        EALinkItem(
            id=link.id,
            source_admin_id=link.source_admin_id,
            target_admin_id=link.target_admin_id,
            source_ea_id=link.source_ea_id,
            source_ea_code=link.source_ea_code,
            status=link.status,
            is_active=link.is_active,
        )
        for link in links
    ]