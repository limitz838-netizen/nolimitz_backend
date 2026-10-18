import os
import random
import string
from datetime import datetime, timedelta
from typing import List, Optional

import requests
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


# =========================
# EMAIL DELIVERY
# =========================
# Keys were generated here and copied into WhatsApp by hand. Nothing about
# generation changes below — this adds the ability to send a key that already
# exists to the address already stored on it.
#
# It is also what the Bachs payment webhook will call later: payment clears, a
# key is generated, this delivers it. Delivery is built and tested first, so
# money is never attached to an untested path.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "NolimitzBots <noreply@nolimitzbots.co.ke>")
CLIENT_APP_URL = os.getenv("CLIENT_APP_URL", "https://nolimitzbots.co.ke")


def send_email(to: str, subject: str, html: str):
    """Send one email through Resend. Returns (ok, detail).

    Errors are RETURNED rather than raised or swallowed. An admin pressing
    "send licence" has to be able to tell sent from looked-like-sent, because
    the alternative is a customer who paid and is waiting on a key that never
    arrived.
    """
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY is not configured"
    if not to or "@" not in to:
        return False, f"invalid recipient: {to!r}"

    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                     "Content-Type": "application/json"},
            json={"from": RESEND_FROM, "to": [to], "subject": subject, "html": html},
            timeout=20,
        )
    except Exception as e:
        return False, f"resend request failed: {e}"

    if r.status_code in (200, 201):
        try:
            return True, r.json().get("id", "sent")
        except Exception:
            return True, "sent"
    return False, f"resend {r.status_code}: {r.text[:200]}"


def licence_email_html(license_key: str, client_name: Optional[str],
                       ea_name: Optional[str], expires_at) -> str:
    """The email a paying customer receives.

    Deliberately plain — no images, no tracking pixels, no marketing markup.
    A licence key that lands in spam is worse than useless, and heavy HTML is
    the fastest way to put it there.
    """
    greeting = f"Hi {client_name}," if client_name else "Hi,"
    expiry = expires_at.strftime("%d %B %Y") if expires_at else "no expiry set"
    robot = ea_name or "your robot"

    return f"""<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;
     max-width:520px;margin:0 auto;color:#111;line-height:1.6">
  <p>{greeting}</p>
  <p>Your {robot} licence key is ready.</p>
  <div style="background:#f6f6f6;border:1px solid #e0e0e0;border-radius:8px;
       padding:18px;margin:22px 0;text-align:center">
    <div style="font-size:12px;letter-spacing:1px;color:#666;
         text-transform:uppercase;margin-bottom:8px">Your licence key</div>
    <div style="font-family:ui-monospace,Consolas,monospace;font-size:22px;
         font-weight:600;letter-spacing:1px">{license_key}</div>
  </div>
  <p><strong>How to activate</strong></p>
  <ol style="padding-left:20px">
    <li>Open <a href="{CLIENT_APP_URL}">{CLIENT_APP_URL}</a> on your phone</li>
    <li>Enter the licence key above</li>
    <li>Add your MT5 login, server and password</li>
    <li>Choose your symbols and lot size</li>
  </ol>
  <p style="color:#555">Valid until: {expiry}</p>
  <p style="color:#555;font-size:14px">Your lot size is always used — never the
  signal provider's. Trades only reach accounts you have connected yourself.</p>
  <hr style="border:none;border-top:1px solid #eee;margin:26px 0">
  <p style="color:#888;font-size:12px">
    Keep this key private. It is tied to one device once activated.<br>
    Trading involves risk of loss. Past performance does not indicate future
    results.
  </p>
</div>"""


def deliver_license(lic, db: Session):
    """Look up the EA name and email the key. Returns (ok, detail)."""
    if not lic.client_email:
        return False, "licence has no client email"
    ea = db.query(ExpertAdvisor).filter(ExpertAdvisor.id == lic.ea_id).first()
    ea_name = getattr(ea, "name", None) if ea else None
    return send_email(
        to=lic.client_email,
        subject=f"Your {ea_name or 'NolimitzBots'} licence key",
        html=licence_email_html(lic.license_key, lic.client_name,
                                ea_name, lic.expires_at),
    )


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
    """Map the dropdown's duration value to an expiry date.

    The dropdown used to offer "30 Days" and "1 Month" as separate options
    while both returned 30 days — the same plan sold twice under two names.
    "30days" is now "15days", a genuinely different length.

    "30days" is still ACCEPTED here on purpose. The frontend is deployed
    separately, so for the window between this going live and Lovable
    shipping, the old value must keep working or every key generated in
    that window fails with "Invalid duration selected". It can be deleted
    once the frontend has been live for a while.
    """
    now = datetime.utcnow()

    if duration == "15days":
        return now + timedelta(days=15)
    elif duration == "1month":
        return now + timedelta(days=30)
    elif duration == "1year":
        return now + timedelta(days=365)
    elif duration == "lifetime":
        return now + timedelta(days=36500)
    elif duration == "30days":
        # Legacy value from the old dropdown. Same as 1month.
        return now + timedelta(days=30)
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

    # QUOTA CHECK. This block appeared twice in the previous revision; the
    # second copy was unreachable, since an over-quota admin already raised.
    if current_admin.role != "super_admin":
        if current_admin.license_used >= current_admin.license_quota:
            raise HTTPException(
                status_code=403,
                detail="License quota exceeded. Request more keys from super admin."
            )

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

        # =========================
        # AI PREMIUM FEATURES
        # =========================
        ai_enabled=True,
        mt5_enabled=True,
        auto_trade_enabled=True,
    )

    db.add(license)
    db.commit()
    db.refresh(license)

    # increment usage AFTER success
    if current_admin.role != "super_admin":
        current_admin.license_used += 1
        db.commit()

    # Deliver it. A failed email must NOT fail this request: the key exists and
    # the quota is already spent, so raising here would leave the admin with a
    # key they think failed to create. They can resend from the licence list.
    email_sent = False
    if license.client_email:
        email_sent, _ = deliver_license(license, db)

    return LicenseResponse(
        message=("License generated and emailed successfully" if email_sent
                 else "License generated successfully"),
        license=LicenseItem(
            id=license.id,
            license_key=license.license_key,
            client_name=license.client_name,
            client_email=license.client_email,
            expires_at=license.expires_at,
            is_active=license.is_active,
            mode_type=license.mode_type,
        )
    )


@router.post("/{license_id}/send-email")
def send_license_email(
    license_id: int,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Email an existing licence key to the address stored on it.

    SCOPED BY ADMIN. Without the ownership check an admin could walk licence
    ids and mail out another tenant's keys. A licence belonging to someone else
    returns 404 rather than 403, so a wrong-tenant id looks identical to one
    that does not exist.
    """
    lic = db.query(License).filter(License.id == license_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="License not found")

    if current_admin.role != "super_admin" and lic.admin_id != current_admin.id:
        raise HTTPException(status_code=404, detail="License not found")

    if not lic.client_email:
        raise HTTPException(
            status_code=400,
            detail="This licence has no client email. Add one before sending.")

    ok, detail = deliver_license(lic, db)
    if not ok:
        # 502 rather than a 200 with a flag — the admin must be able to see
        # that it did not send.
        raise HTTPException(status_code=502, detail=f"Email not sent - {detail}")

    return {
        "success": True,
        "message": f"License key sent to {lic.client_email}",
        "email_id": detail,
    }


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
