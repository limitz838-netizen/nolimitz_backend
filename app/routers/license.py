import random
import string
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Admin, ExpertAdvisor, License, AdminProfile
from app.schemas import (
    LicenseCreateRequest,
    LicenseItem,
    LicenseResponse,
)
from app.auth import decode_access_token

router = APIRouter(prefix="/licenses", tags=["Licenses"])


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

    admin = db.query(Admin).filter(Admin.id == payload.get("admin_id")).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    return admin


def generate_license_key(db: Session):
    while True:
        random_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
        key = f"NL-{random_part}"

        existing = db.query(License).filter(License.license_key == key).first()
        if not existing:
            return key


def calculate_expiry(duration: str) -> Optional[datetime]:
    now = datetime.utcnow()

    if duration == "30days":
        return now + timedelta(days=30)
    elif duration == "1month":
        return now + timedelta(days=30)
    elif duration == "1year":
        return now + timedelta(days=365)
    elif duration == "lifetime":
        return now + timedelta(days=36500)
    else:
        raise HTTPException(status_code=400, detail="Invalid duration selected")


@router.post("/generate", response_model=LicenseResponse)
def generate_license(
    payload: LicenseCreateRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    ea = db.query(ExpertAdvisor).filter(
        ExpertAdvisor.id == payload.ea_id,
        ExpertAdvisor.admin_id == current_admin.id
    ).first()

    if not ea:
        raise HTTPException(status_code=404, detail="EA not found")

    profile = db.query(AdminProfile).filter(
        AdminProfile.admin_id == current_admin.id
    ).first()

    branding = {
        "admin_code": current_admin.admin_code,
        "display_name": profile.display_name if profile else None,
        "logo_url": profile.logo_url if profile else None,
        "support_email": profile.support_email if profile else None,
        "phone": profile.phone if profile else None,
        "telegram": profile.telegram if profile else None,
        "whatsapp": profile.whatsapp if profile else None,
        "company_name": profile.company_name if profile else None,
    }

    expires_at = calculate_expiry(payload.duration)

    license = License(
       admin_id=current_admin.id,
       ea_id=ea.id,
       license_key=generate_license_key(db),
       client_name=payload.client_name,
       client_email=payload.client_email,
       mode_type="both",
       expires_at=expires_at,
       is_active=True,
       branding_snapshot=branding,
    )

    db.add(license)
    db.commit()
    db.refresh(license)

    return LicenseResponse(
        message="License generated successfully",
        license=LicenseItem(
            id=license.id,
            license_key=license.license_key,
            client_name=license.client_name,
            client_email=license.client_email,
            expires_at=license.expires_at,
            is_active=license.is_active,
            mode_type=license.mode_type,  # ✅ ADD THIS
        )
    )


@router.get("/", response_model=List[LicenseItem])
def list_licenses(
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    licenses = db.query(License).filter(
        License.admin_id == current_admin.id
    ).order_by(License.id.desc()).all()

    return [
        LicenseItem(
            id=l.id,
            license_key=l.license_key,
            client_name=l.client_name,
            client_email=l.client_email,
            expires_at=l.expires_at,
            is_active=l.is_active,
            mode_type=l.mode_type,
        )
        for l in licenses
    ]


@router.post("/{license_id}/deactivate")
def deactivate_license(
    license_id: int,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    license = db.query(License).filter(
        License.id == license_id,
        License.admin_id == current_admin.id
    ).first()

    if not license:
        raise HTTPException(status_code=404, detail="License not found")

    license.is_active = False
    db.commit()

    return {"message": "License deactivated"}