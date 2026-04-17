import os
import shutil
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models import Admin, AdminProfile, License, MasterAccount
from app.schemas import (
    AdminApprovalResponse,
    AdminListItem,
    AdminLoginRequest,
    AdminMeResponse,
    AdminProfileUpdateRequest,
    AdminSignupRequest,
    BasicMessageResponse,
    DeviceLockResetResponse,
    LogoUploadResponse,
    TokenResponse,
)

router = APIRouter(prefix="/admin", tags=["Admin"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# =========================
# HELPERS
# =========================
def require_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    return authorization.split(" ", 1)[1].strip()


def get_current_admin(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> Admin:
    token = require_bearer_token(authorization)

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    admin_id = payload.get("admin_id")
    if not admin_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    admin = db.query(Admin).filter(Admin.id == admin_id).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    if not admin.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    return admin


def get_current_super_admin(current_admin: Admin = Depends(get_current_admin)) -> Admin:
    if current_admin.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    return current_admin


def get_current_approved_admin(current_admin: Admin = Depends(get_current_admin)) -> Admin:
    if current_admin.role != "super_admin" and not current_admin.is_approved:
        raise HTTPException(status_code=403, detail="Your account is pending approval")
    return current_admin


def get_or_create_admin_profile(admin_id: int, db: Session) -> AdminProfile:
    profile = db.query(AdminProfile).filter(AdminProfile.admin_id == admin_id).first()
    if profile:
        return profile

    profile = AdminProfile(admin_id=admin_id)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def generate_admin_code(db: Session) -> int:
    last_admin = db.query(Admin).order_by(Admin.admin_code.desc()).first()

    if not last_admin or not last_admin.admin_code:
        return 100

    return int(last_admin.admin_code) + 1


def build_admin_me_response(admin: Admin, profile: Optional[AdminProfile]) -> AdminMeResponse:
    return AdminMeResponse(
        admin_id=admin.id,
        admin_code=admin.admin_code,
        full_name=admin.full_name,
        email=admin.email,
        role=admin.role,
        is_approved=admin.is_approved,
        is_active=admin.is_active,
        display_name=profile.display_name if profile else None,
        logo_url=profile.logo_url if profile else None,
        phone=profile.phone if profile else None,
        support_email=profile.support_email if profile else None,
        telegram=profile.telegram if profile else None,
        whatsapp=profile.whatsapp if profile else None,
        company_name=profile.company_name if profile else None,
    )


def build_admin_list_item(admin: Admin) -> AdminListItem:
    return AdminListItem(
        admin_id=admin.id,
        full_name=admin.full_name,
        email=admin.email,
        role=admin.role,
        is_approved=admin.is_approved,
        is_active=admin.is_active,
    )


# =========================
# AUTH
# =========================
@router.post("/signup", response_model=BasicMessageResponse)
def admin_signup(payload: AdminSignupRequest, db: Session = Depends(get_db)):
    existing = db.query(Admin).filter(Admin.email == payload.email.strip().lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    admin = Admin(
        admin_code=generate_admin_code(db),
        full_name=payload.full_name.strip(),
        email=payload.email.strip().lower(),
        password_hash=hash_password(payload.password),
        role="admin",
        is_approved=False,
        is_active=True,
    )

    db.add(admin)
    db.commit()
    db.refresh(admin)

    profile = AdminProfile(admin_id=admin.id)
    db.add(profile)
    db.commit()

    return BasicMessageResponse(
        message="Signup successful. Your account is pending approval."
    )


@router.post("/login", response_model=TokenResponse)
def admin_login(payload: AdminLoginRequest, db: Session = Depends(get_db)):
    admin = db.query(Admin).filter(
        Admin.email == payload.email.strip().lower()
    ).first()

    if not admin:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not admin.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    token = create_access_token(
        {"admin_id": admin.id, "email": admin.email, "role": admin.role}
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        admin_id=admin.id,
        admin_code=int(admin.admin_code),
        full_name=admin.full_name,
        email=admin.email,
        role=admin.role,
        is_approved=admin.is_approved,
    )


# =========================
# PROFILE
# =========================
@router.get("/me", response_model=AdminMeResponse)
def admin_me(
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    profile = db.query(AdminProfile).filter(
        AdminProfile.admin_id == current_admin.id
    ).first()

    return build_admin_me_response(current_admin, profile)


@router.put("/profile", response_model=BasicMessageResponse)
def update_profile(
    payload: AdminProfileUpdateRequest,
    current_admin: Admin = Depends(get_current_approved_admin),
    db: Session = Depends(get_db),
):
    profile = get_or_create_admin_profile(current_admin.id, db)

    if payload.display_name is not None:
        profile.display_name = payload.display_name.strip()
    if payload.phone is not None:
        profile.phone = payload.phone.strip()
    if payload.support_email is not None:
        profile.support_email = str(payload.support_email).strip().lower()
    if payload.telegram is not None:
        profile.telegram = payload.telegram.strip()
    if payload.whatsapp is not None:
        profile.whatsapp = payload.whatsapp.strip()
    if payload.company_name is not None:
        profile.company_name = payload.company_name.strip()

    db.commit()

    return BasicMessageResponse(message="Profile updated successfully")


@router.post("/profile/logo", response_model=LogoUploadResponse)
def upload_logo(
    file: UploadFile = File(...),
    current_admin: Admin = Depends(get_current_approved_admin),
    db: Session = Depends(get_db),
):
    allowed_types = ["image/png", "image/jpeg", "image/jpg", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Only PNG, JPG, JPEG, and WEBP images are allowed",
        )

    ext = os.path.splitext(file.filename)[1].lower()
    safe_filename = f"admin_{current_admin.id}_logo{ext}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    profile = get_or_create_admin_profile(current_admin.id, db)
    profile.logo_url = f"/uploads/{safe_filename}"
    db.commit()

    return LogoUploadResponse(
        message="Logo uploaded successfully",
        logo_url=profile.logo_url,
    )


@router.get("/dashboard", response_model=BasicMessageResponse)
def admin_dashboard(current_admin: Admin = Depends(get_current_approved_admin)):
    return BasicMessageResponse(message="Welcome to NolimitzBots admin dashboard")


# =========================
# SUPER ADMIN
# =========================
@router.get("/pending-admins", response_model=List[AdminListItem])
def list_pending_admins(
    current_admin: Admin = Depends(get_current_super_admin),
    db: Session = Depends(get_db),
):
    admins = (
        db.query(Admin)
        .filter(Admin.role == "admin", Admin.is_approved == False)
        .order_by(Admin.id.desc())
        .all()
    )

    return [build_admin_list_item(admin) for admin in admins]


@router.post("/approve/{admin_id}", response_model=AdminApprovalResponse)
def approve_admin(
    admin_id: int,
    current_admin: Admin = Depends(get_current_super_admin),
    db: Session = Depends(get_db),
):
    admin = db.query(Admin).filter(
        Admin.id == admin_id,
        Admin.role == "admin",
    ).first()

    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    admin.is_approved = True
    db.commit()

    return AdminApprovalResponse(
        message="Admin approved successfully",
        admin_id=admin.id,
        is_approved=admin.is_approved,
    )


@router.post("/deactivate/{admin_id}", response_model=BasicMessageResponse)
def deactivate_admin(
    admin_id: int,
    current_admin: Admin = Depends(get_current_super_admin),
    db: Session = Depends(get_db),
):
    admin = db.query(Admin).filter(
        Admin.id == admin_id,
        Admin.role == "admin",
    ).first()

    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    admin.is_active = False
    db.commit()

    return BasicMessageResponse(message="Admin deactivated successfully")


@router.get("/all-admins", response_model=List[AdminListItem])
def list_all_admins(
    current_admin: Admin = Depends(get_current_super_admin),
    db: Session = Depends(get_db),
):
    admins = db.query(Admin).order_by(Admin.id.desc()).all()
    return [build_admin_list_item(admin) for admin in admins]


# =========================
# LICENSE TOOLS
# =========================
@router.post("/licenses/{license_id}/reset-device-lock", response_model=DeviceLockResetResponse)
def reset_license_device_lock(
    license_id: int,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    license_row = db.query(License).filter(License.id == license_id).first()
    if not license_row:
        raise HTTPException(status_code=404, detail="License not found")

    if current_admin.role != "super_admin" and license_row.admin_id != current_admin.id:
        raise HTTPException(status_code=403, detail="Not allowed to reset this license")

    license_row.activated_device_id = None
    license_row.activated_device_name = None
    license_row.first_activated_at = None
    license_row.last_seen_at = None

    db.commit()
    db.refresh(license_row)

    return DeviceLockResetResponse(
        message="Device lock reset successfully",
        license_key=license_row.license_key,
        activated_device_id=license_row.activated_device_id,
        activated_device_name=license_row.activated_device_name,
    )


# =========================
# MASTER MT5 ACCOUNT
# =========================
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