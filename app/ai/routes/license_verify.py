from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.database import get_db
from app.models import License

router = APIRouter(
    prefix="/api/client",
    tags=["License Verification"]
)


@router.post("/verify-license")
def verify_license(
    data: dict,
    db: Session = Depends(get_db)
):

    license_key = data.get("license_key")

    device_id = data.get("device_id")

    if not license_key:

        raise HTTPException(
            status_code=400,
            detail="License key required"
        )

    # =========================
    # FIND LICENSE
    # =========================

    license = (
        db.query(License)
        .filter(
            License.license_key == license_key
        )
        .first()
    )

    if not license:

        raise HTTPException(
            status_code=404,
            detail="Invalid license key"
        )

    # =========================
    # CHECK ACTIVE
    # =========================

    if not license.is_active:

        raise HTTPException(
            status_code=403,
            detail="License inactive"
        )

    # =========================
    # CHECK EXPIRY
    # =========================

    now = datetime.now(timezone.utc)

    if license.expires_at < now:

        raise HTTPException(
            status_code=403,
            detail="License expired"
        )
    
    # =========================
    # DEVICE LOCK
    # =========================

    if not license.device_id:

        license.device_id = device_id

        db.commit()

    else:

         if license.device_id != device_id:

              return {
                 "success": False,
                 "message": "License already used on another device"
              }

    # =========================
    # RETURN FEATURES
    # =========================

    return {

        "success": True,

        "license_key":
            license.license_key,

        "client_name":
            license.client_name,

        "ai_enabled":
            license.ai_enabled,

        "mt5_enabled":
            license.mt5_enabled,

        "auto_trade_enabled":
            license.auto_trade_enabled,

        "expires_at":
            license.expires_at
    }