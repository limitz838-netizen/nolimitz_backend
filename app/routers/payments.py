"""
================================================================================
  BACHS PAYMENT WEBHOOK  —  app/routers/payments.py
================================================================================

  WHAT THIS FIXES
  Bachs has been delivering collection.succeeded events since 7 July. They went
  to a Supabase edge function that returned 200 and did nothing, so every
  customer who paid had to be sent their key by hand. Bachs also tried
  /payments/bachs-webhook on this backend and got 404, because the route did
  not exist. This is that route.

  No payment is lost: every failed delivery is still in the Bachs dashboard
  with a Retry button. Once this deploys, retrying replays them.

  ── DURATION COMES FROM plan_id, NOT PRICE ───────────────────────────────────
  Confirmed from live events:
      metadata.plan_id = "monthly"   -> 30 days   ($18.99)
      metadata.plan_id = "annual"    -> 365 days  ($79.99)
      metadata.plan_id = "lifetime"  -> lifetime  ($170.00)

  Price is deliberately NOT the discriminator. Two live payments settled at
  $9.99 on a promotional payment link while still being monthly plans — an
  amount-based mapping would have mispriced those, and a customer given the
  wrong expiry is worse than one who waits for a manual key.

  An UNKNOWN plan_id creates no licence and returns 200 with a flag. Returning
  an error would make Bachs retry forever; creating a guessed licence would
  give someone the wrong access. Neither is acceptable, so it records the
  payment and tells you.

  ── SETUP ────────────────────────────────────────────────────────────────────
  Render environment:
      BACHS_WEBHOOK_SECRET = whsec_...        (already added)
      BACHS_EA_ID          = 1                (NOLIMITZ PRO — end users only
                                               ever buy this EA)
      BACHS_ADMIN_ID       = 2                (owning admin for created keys)

  Then in the Bachs dashboard set the webhook URL to:
      https://nolimitz-backend-yfne.onrender.com/payments/bachs-webhook

  And in app/main.py:
      from app.routers import payments
      app.include_router(payments.router)
================================================================================
"""

import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Admin, ExpertAdvisor, License
from app.routers.license import deliver_license, generate_license_key

router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger("payments")

BACHS_WEBHOOK_SECRET = os.getenv("BACHS_WEBHOOK_SECRET", "")
BACHS_EA_ID = int(os.getenv("BACHS_EA_ID", "1"))
BACHS_ADMIN_ID = int(os.getenv("BACHS_ADMIN_ID", "2"))

# plan_id -> days. "lifetime" matches what license.py already does for its
# lifetime option, so a key bought here behaves like one issued by hand.
PLAN_DAYS = {
    "monthly": 30,
    "month": 30,
    "30days": 30,
    "annual": 365,
    "yearly": 365,
    "year": 365,
    "lifetime": 36500,
}

# What each plan SHOULD cost, used only as a sanity check in the log. A
# mismatch is worth seeing (promo link, price change, or something wrong) but
# must never change the duration — plan_id is the authority.
PLAN_EXPECTED_USD = {"monthly": 18.99, "annual": 79.99, "lifetime": 170.00}


def _candidate_signatures(raw_body: bytes) -> dict:
    """Every plausible HMAC scheme, labelled.

    Bachs documents only "payloads are signed using HMAC" and names the
    Bachs-Signature header — it does not say hex or base64, whether the signed
    payload includes a timestamp, or whether the whsec_ prefix is part of the
    key. Providers differ on all three. Rather than guess and get 401s with no
    way to see why, every variant is computed and the matching one is logged by
    name the first time it succeeds.

    Once the log names the winner, the rest can be deleted — but leaving them
    costs microseconds and survives Bachs changing the scheme.
    """
    import base64

    secrets = [BACHS_WEBHOOK_SECRET]
    if BACHS_WEBHOOK_SECRET.startswith("whsec_"):
        # Some providers treat the prefix as a label, not part of the key.
        secrets.append(BACHS_WEBHOOK_SECRET[len("whsec_"):])

    out = {}
    for idx, secret in enumerate(secrets):
        tag = "withprefix" if idx == 0 else "noprefix"
        key = secret.encode()
        mac = hmac.new(key, raw_body, hashlib.sha256)
        out[f"{tag}_hex"] = mac.hexdigest()
        out[f"{tag}_b64"] = base64.b64encode(mac.digest()).decode()

        # Base64-decoded secret, used by some providers that issue the shared
        # secret already encoded.
        try:
            raw_key = base64.b64decode(secret + "=" * (-len(secret) % 4))
            if raw_key:
                m2 = hmac.new(raw_key, raw_body, hashlib.sha256)
                out[f"{tag}_b64key_hex"] = m2.hexdigest()
                out[f"{tag}_b64key_b64"] = base64.b64encode(m2.digest()).decode()
        except Exception:
            pass

    return out


def verify_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    """Check the Bachs-Signature header against the shared secret.

    compare_digest, not ==. A plain comparison returns faster when the first
    byte differs than when the last does, which leaks the correct signature one
    byte at a time to anyone willing to send enough requests.

    The RAW body matters: re-serialising parsed JSON changes whitespace and key
    order, and the hash no longer matches.

    Set BACHS_DEBUG_SIG=true in Render to log the prefix of every computed
    variant alongside the received header. That is a debugging aid, not a
    permanent setting — it puts signature material in the logs, so turn it off
    once the scheme is known.
    """
    if not BACHS_WEBHOOK_SECRET:
        logger.error("BACHS_WEBHOOK_SECRET is not set — refusing all webhooks")
        return False
    if not signature:
        logger.warning("no Bachs-Signature header on request")
        return False

    # Header may be bare, "sha256=<sig>", or a comma-joined list during a
    # secret rotation. Also handles "t=<ts>,v1=<sig>" shapes by taking the part
    # after each "=".
    received = []
    for part in signature.split(","):
        part = part.strip()
        if "=" in part:
            part = part.split("=", 1)[1].strip()
        if part:
            received.append(part)

    variants = _candidate_signatures(raw_body)

    for name, expected in variants.items():
        for got in received:
            if hmac.compare_digest(expected, got):
                logger.info("signature verified using variant: %s", name)
                return True

    if os.getenv("BACHS_DEBUG_SIG", "").lower() == "true":
        logger.error("SIGNATURE MISMATCH — header=%r  body_len=%d",
                     signature[:100], len(raw_body))
        for name, expected in variants.items():
            logger.error("   computed %-20s -> %s...", name, expected[:20])
        for got in received:
            logger.error("   received %s...", got[:20])

    return False


@router.post("/bachs-webhook")
async def bachs_webhook(
    request: Request,
    bachs_signature: str = Header(None, alias="Bachs-Signature"),
    db: Session = Depends(get_db),
):
    """Payment succeeded -> create a licence -> email it.

    Returns 200 for anything it has decided about, including events it
    deliberately ignores. A non-2xx makes Bachs retry, so an error response is
    reserved for cases where retrying could actually help: a bad signature, or
    a database failure mid-way.
    """
    raw = await request.body()

    if not verify_signature(raw, bachs_signature):
        # 401, not 200. This endpoint mints licence keys — an unsigned caller
        # who found the URL must get nothing.
        logger.warning("rejected webhook with bad or missing signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body is not JSON")

    event_type = event.get("type")
    event_id = event.get("id")

    if event_type != "collection.succeeded":
        # failed, abandoned, etc. Nothing to do, but acknowledge so Bachs stops.
        logger.info("ignoring %s (%s)", event_type, event_id)
        return {"received": True, "ignored": event_type}

    data = event.get("data") or {}
    meta = data.get("metadata") or {}
    customer = data.get("customer") or {}

    charge_id = data.get("charge_id")
    email = (customer.get("email") or "").strip()
    name = (customer.get("name") or "").strip() or None
    plan_id = (meta.get("plan_id") or "").strip().lower()
    settlement = data.get("settlement_amount")

    if not email:
        logger.error("payment %s has no customer email — cannot deliver", charge_id)
        return {"received": True, "error": "no customer email"}

    # ---- IDEMPOTENCY --------------------------------------------------------
    # Bachs retries on any non-2xx, and you may also press Retry by hand on the
    # backlog of 404'd events. Without this, one payment could mint several
    # keys. The charge id is stored in the branding snapshot because there is
    # no payments table — crude, but it makes the check real rather than
    # theoretical.
    if charge_id:
        existing = db.query(License).filter(
            License.branding_snapshot["bachs_charge_id"].astext == str(charge_id)
        ).first()
        if existing:
            logger.info("charge %s already issued licence %s — not duplicating",
                        charge_id, existing.id)
            return {"received": True, "duplicate": True,
                    "license_key": existing.license_key}

    days = PLAN_DAYS.get(plan_id)
    if days is None:
        # Do NOT guess. A wrong expiry is worse than a manual key: the customer
        # either loses access early or gets more than they paid for, and
        # neither is visible until they complain.
        logger.error("UNKNOWN plan_id %r on charge %s (%s, %s) — no licence "
                     "created, issue this one by hand",
                     plan_id, charge_id, email, settlement)
        return {"received": True, "error": f"unknown plan_id: {plan_id}",
                "action_required": "issue this licence manually"}

    # Price sanity check — logged, never acted on.
    try:
        expected = PLAN_EXPECTED_USD.get(plan_id)
        if expected and settlement and abs(float(settlement) - expected) > 0.5:
            logger.warning("charge %s: plan %s settled at %s, expected ~%s "
                           "(promo link or price change?)",
                           charge_id, plan_id, settlement, expected)
    except Exception:
        pass

    ea = db.query(ExpertAdvisor).filter(ExpertAdvisor.id == BACHS_EA_ID).first()
    if not ea:
        # Retryable: the EA may be restored. 500 makes Bachs try again rather
        # than dropping a paid customer on the floor.
        logger.error("BACHS_EA_ID %s not found — cannot issue licence", BACHS_EA_ID)
        raise HTTPException(status_code=500, detail="Configured EA not found")

    admin = db.query(Admin).filter(Admin.id == BACHS_ADMIN_ID).first()

    lic = License(
        admin_id=BACHS_ADMIN_ID,
        ea_id=ea.id,
        license_key=generate_license_key(db),
        client_name=name,
        client_email=email,
        mode_type="both",
        expires_at=datetime.utcnow() + timedelta(days=days),
        is_active=True,
        branding_snapshot={
            "admin_code": getattr(admin, "admin_code", None),
            "display_name": "NolimitzBots",
            # Provenance. Also what the duplicate check above reads.
            "bachs_charge_id": str(charge_id) if charge_id else None,
            "bachs_event_id": event_id,
            "bachs_plan_id": plan_id,
            "bachs_settlement": str(settlement) if settlement else None,
            "source": "bachs",
        },
        ai_enabled=True,
        mt5_enabled=True,
        auto_trade_enabled=True,
    )

    db.add(lic)
    db.commit()
    db.refresh(lic)

    logger.info("licence %s (%s) created for %s — plan %s, %s days, charge %s",
                lic.id, lic.license_key, email, plan_id, days, charge_id)

    # Deliver it. A failed email must NOT fail the webhook: the licence exists
    # and is valid, so making Bachs retry would only risk a duplicate. It is
    # logged loudly and can be resent from the licence list.
    sent, detail = deliver_license(lic, db)
    if not sent:
        logger.error("licence %s created but EMAIL FAILED (%s) — resend from "
                     "the dashboard", lic.id, detail)

    return {
        "received": True,
        "license_id": lic.id,
        "license_key": lic.license_key,
        "plan": plan_id,
        "days": days,
        "emailed": sent,
    }


@router.get("/bachs-webhook")
def bachs_webhook_health():
    """So a browser hit tells you the route exists.

    The 404s in the Bachs dashboard were the whole problem; being able to check
    the path is live in one click is worth the four lines.
    """
    return {
        "status": "ready",
        "secret_configured": bool(BACHS_WEBHOOK_SECRET),
        "ea_id": BACHS_EA_ID,
        "plans": sorted(set(PLAN_DAYS)),
    }
        "status": "ready",
        "secret_configured": bool(BACHS_WEBHOOK_SECRET),
        "ea_id": BACHS_EA_ID,
        "plans": sorted(set(PLAN_DAYS)),
    }
