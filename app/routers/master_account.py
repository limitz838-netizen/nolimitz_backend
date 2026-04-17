import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import decode_access_token
from app.database import get_db
from app.models import Admin
from app.routers.admin import get_current_approved_admin

router = APIRouter(prefix="/admin/master-account", tags=["Master Account"])


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "storage")
DATA_FILE = os.path.join(DATA_DIR, "master_accounts.json")

os.makedirs(DATA_DIR, exist_ok=True)


class MasterAccountSaveRequest(BaseModel):
    ea_id: int
    mt_login: str
    mt_password: str
    mt_server: str


class MasterAccountVerifyRequest(BaseModel):
    ea_id: int
    mt_login: str
    mt_password: str
    mt_server: str


def read_storage() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_storage(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_current_admin(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> Admin:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid authorization header",
        )

    token = authorization.split(" ")[1]
    payload = decode_access_token(token)

    admin_id = payload.get("admin_id")
    if not admin_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    admin = db.query(Admin).filter(Admin.id == admin_id).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    return admin


@router.post("/master-account/save")
def save_master_account(
    data: dict,
    current_admin: Admin = Depends(get_current_approved_admin),
    db: Session = Depends(get_db),
):
    ea_id = data.get("ea_id")
    mt_login = data.get("mt_login")
    mt_password = data.get("mt_password")
    mt_server = data.get("mt_server")

    if not ea_id or not mt_login or not mt_password or not mt_server:
        raise HTTPException(status_code=400, detail="All fields are required")

    account = db.query(MasterAccount).filter_by(admin_id=current_admin.id).first()

    if not account:
        account = MasterAccount(
            admin_id=current_admin.id,
            ea_id=int(ea_id),
            mt_login=str(mt_login),
            mt_password=str(mt_password),
            mt_server=str(mt_server),
            is_connected=False,
            account_name=None,
            broker_name=None,
        )
        db.add(account)
    else:
        account.ea_id = int(ea_id)
        account.mt_login = str(mt_login)
        account.mt_password = str(mt_password)
        account.mt_server = str(mt_server)
        account.is_connected = False
        account.account_name = None
        account.broker_name = None

    db.commit()
    db.refresh(account)

    return {
        "success": True,
        "message": "Master account saved. Waiting for bridge connection...",
        "connected": False,
        "ea_id": account.ea_id,
        "mt_login": account.mt_login,
        "mt_server": account.mt_server,
    }


@router.post("/master-account/connected")
def mark_master_connected(
    data: dict,
    current_admin: Admin = Depends(get_current_approved_admin),
    db: Session = Depends(get_db),
):
    account = db.query(MasterAccount).filter_by(admin_id=current_admin.id).first()

    if not account:
        raise HTTPException(status_code=404, detail="Master account not found")

    account_name = data.get("account_name")
    broker_name = data.get("broker_name")

    account.is_connected = True
    account.account_name = account_name
    account.broker_name = broker_name

    db.commit()
    db.refresh(account)

    return {
        "success": True,
        "message": "Master account connected",
        "connected": True,
        "account_name": account.account_name,
        "broker_name": account.broker_name,
    }


@router.get("/master-account/status")
def get_master_account_status(
    current_admin: Admin = Depends(get_current_approved_admin),
    db: Session = Depends(get_db),
):
    account = db.query(MasterAccount).filter_by(admin_id=current_admin.id).first()

    if not account:
        return {
            "connected": False,
            "is_connected": False,
            "message": "No master account saved yet",
        }

    return {
        "connected": bool(account.is_connected),
        "is_connected": bool(account.is_connected),
        "ea_id": account.ea_id,
        "mt_login": account.mt_login,
        "mt_password": account.mt_password,
        "mt_server": account.mt_server,
        "account_name": account.account_name,
        "broker_name": account.broker_name,
    }


@router.get("/master-account")
def get_master_account(
    current_admin: Admin = Depends(get_current_approved_admin),
    db: Session = Depends(get_db),
):
    account = db.query(MasterAccount).filter_by(admin_id=current_admin.id).first()

    if not account:
        return {"connected": False, "is_connected": False}

    return {
        "connected": bool(account.is_connected),
        "is_connected": bool(account.is_connected),
        "ea_id": account.ea_id,
        "mt_login": account.mt_login,
        "mt_server": account.mt_server,
        "account_name": account.account_name,
        "broker_name": account.broker_name,
    }