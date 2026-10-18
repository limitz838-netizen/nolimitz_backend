"""
================================================================================
  MASTER ACCOUNT ROUTER  —  each admin connects their own master MT5 account
================================================================================

  ── FIXES IN THIS REVISION ───────────────────────────────────────────────────
  1. mt_password IS NO LONGER RETURNED TO THE BROWSER. /status and GET "" both
     included the admin's live MT5 password in their JSON response, so it
     travelled to the frontend on every dashboard load — into browser memory,
     the network tab, and whatever the frontend logs. Nothing in the UI needs
     it. It is now returned ONLY to the worker endpoint below, which is
     machine-to-machine behind the shared worker token.

  2. WORKER ENDPOINTS ADDED. Nothing could read this table except a logged-in
     admin, so master_mt5_bridge.py had no way to discover which masters exist
     and no way to report that it had connected. That is why saving a master
     account in the dashboard did nothing: the row was stored and never read.

         GET  /admin/master-account/worker/list
              every connected master, with credentials, for the bridge

         POST /admin/master-account/worker/connected
              the bridge reports a successful login, by mt_login rather than
              by admin — machines have no admin identity

  3. Dead file-storage helpers removed. read_storage() / write_storage() wrote
     to storage/master_accounts.json, which nothing read; /save already used
     the MasterAccount table. On Render that directory is ephemeral anyway, so
     anything written there vanished on the next deploy. The os.makedirs at
     import time went with them.

  ── STILL OUTSTANDING, DELIBERATELY NOT DONE HERE ────────────────────────────
  mt_password is stored in PLAIN TEXT. That is tolerable while the only row is
  the platform owner's own account; it is not tolerable once other admins save
  theirs, because you would then be holding third-party live trading
  credentials in clear text. Encrypting it needs the Fernet key wired and
  verified on both the API and the VPS (the NOLIMITZ_CRED_KEY /
  NOLIMITZ_FERNET_KEY name mismatch has silently defeated encryption here
  before), so it is a separate change with its own test — not a quiet edit
  buried in this one.
================================================================================
"""

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import decode_access_token
from app.database import get_db
from app.models import Admin, MasterAccount
from app.routers.admin import get_current_approved_admin

router = APIRouter(prefix="/admin/master-account", tags=["Master Account"])


# =========================
# WORKER AUTH
# =========================
# Same shared secret the /worker/* routes and the copier event routes use.
# Defined locally rather than imported from another router — a cross-router
# import once hung a deploy before it bound a port, so each router stays
# self-contained.
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")


def require_worker_token(x_worker_token: str = Header(None)):
    """Shared secret for routes no browser should ever reach."""
    if not WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="WORKER_TOKEN is not configured")
    if x_worker_token != WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid worker token")
    return True


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


class WorkerConnectedRequest(BaseModel):
    mt_login: str
    connected: bool = True
    account_name: Optional[str] = None
    broker_name: Optional[str] = None
    error: Optional[str] = None


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


def _public_view(account: MasterAccount) -> dict:
    """What the dashboard is allowed to see.

    mt_password is deliberately absent. The admin typed it; they do not need it
    read back, and sending it to a browser is how credentials end up in logs.
    """
    return {
        "connected": bool(account.is_connected),
        "is_connected": bool(account.is_connected),
        "ea_id": account.ea_id,
        "mt_login": account.mt_login,
        "mt_server": account.mt_server,
        "account_name": account.account_name,
        "broker_name": account.broker_name,
    }


# =========================
# ADMIN ROUTES
# =========================
@router.post("/save")
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

    # One master per admin. If an admin ever needs a second EA with its own
    # master, this filter and the model's uniqueness assumption both change —
    # worth deciding before that admin exists rather than after.
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
        # Credentials changed, so the previous connection proves nothing.
        account.is_connected = False
        account.account_name = None
        account.broker_name = None

    db.commit()
    db.refresh(account)

    return {
        "success": True,
        "message": "Master account saved. Waiting for bridge connection...",
        **_public_view(account),
    }


@router.post("/connected")
def mark_master_connected(
    data: dict,
    current_admin: Admin = Depends(get_current_approved_admin),
    db: Session = Depends(get_db),
):
    """Kept for the existing frontend flow. The bridge uses the worker route
    below instead, because a machine has no admin login."""
    account = db.query(MasterAccount).filter_by(admin_id=current_admin.id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Master account not found")

    account.is_connected = True
    account.account_name = data.get("account_name")
    account.broker_name = data.get("broker_name")
    db.commit()
    db.refresh(account)

    return {
        "success": True,
        "message": "Master account connected",
        **_public_view(account),
    }


@router.get("/status")
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
    return _public_view(account)


@router.get("")
def get_master_account(
    current_admin: Admin = Depends(get_current_approved_admin),
    db: Session = Depends(get_db),
):
    account = db.query(MasterAccount).filter_by(admin_id=current_admin.id).first()
    if not account:
        return {"connected": False, "is_connected": False}
    return _public_view(account)


@router.delete("")
def delete_master_account(
    current_admin: Admin = Depends(get_current_approved_admin),
    db: Session = Depends(get_db),
):
    """Disconnect a master. The admin's own row only — an admin can never
    reach another tenant's credentials through this router."""
    account = db.query(MasterAccount).filter_by(admin_id=current_admin.id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Master account not found")
    db.delete(account)
    db.commit()
    return {"success": True, "message": "Master account removed"}


# =========================
# WORKER ROUTES  (machine-only)
# =========================
@router.get("/worker/list")
def worker_list_master_accounts(
    _: bool = Depends(require_worker_token),
    db: Session = Depends(get_db),
):
    """Every saved master account, for master_mt5_bridge.py.

    This is the ONLY place mt_password leaves the database, and it goes to a
    machine over HTTPS behind the shared worker token — never to a browser.

    The bridge watches one MT5 terminal per row returned here, so this list
    IS the set of admins whose master trades reach their clients. An admin who
    saves credentials and never appears in a bridge log has no watcher running.
    """
    rows = db.query(MasterAccount).order_by(MasterAccount.id.asc()).all()
    return {
        "count": len(rows),
        "masters": [
            {
                "id": r.id,
                "admin_id": r.admin_id,
                "ea_id": r.ea_id,
                "mt_login": r.mt_login,
                "mt_password": r.mt_password,
                "mt_server": r.mt_server,
                "is_connected": bool(r.is_connected),
                "account_name": r.account_name,
                "broker_name": r.broker_name,
            }
            for r in rows
        ],
    }


@router.post("/worker/connected")
def worker_mark_connected(
    payload: WorkerConnectedRequest,
    _: bool = Depends(require_worker_token),
    db: Session = Depends(get_db),
):
    """The bridge reports whether it could log into a master.

    Keyed on mt_login, not admin_id — a worker has no admin identity. This is
    what makes the dashboard's "Connected" badge reflect reality instead of
    whatever the browser last asserted.
    """
    account = db.query(MasterAccount).filter_by(
        mt_login=str(payload.mt_login)).first()
    if not account:
        raise HTTPException(status_code=404, detail="Master account not found")

    account.is_connected = bool(payload.connected)
    if payload.account_name is not None:
        account.account_name = payload.account_name
    if payload.broker_name is not None:
        account.broker_name = payload.broker_name

    db.commit()
    db.refresh(account)

    return {
        "success": True,
        "mt_login": account.mt_login,
        "connected": bool(account.is_connected),
    }
