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

  ── DURATION IS RESOLVED, NOT GUESSED ────────────────────────────────────────
  Plan resolution now lives in app/routers/bachs_plans.py. This file asks it
  and does what it says. The order there is:

      1. metadata.plan_id      hosted checkout sets it
      2. payment link id       via BACHS_LINK_PLANS   (precise)
      3. settlement amount     via BACHS_AMOUNT_PLANS (blunt, opt-in)
      4. refuse                no licence, 200 + action_required

  WHY: hosted checkout sends plan_id, payment links do not. On 23 September
  four people paid through links and this endpoint refused all four, correctly,
  because it had nothing to go on. Rules 2 and 3 give it something.

  A CORRECTION worth keeping. An earlier version of this comment said $9.99 was
  a promotional MONTHLY link. It is not — $9.99 is the 15-day plan. Anything
  built on that stale note would have handed every $9.99 customer 30 days for a
  15-day payment. Prices and durations live in Render env vars now, set from
  the actual links, and not in a comment that can quietly go out of date:

      15 days   $9.99    pl_28c868d8f88b
      30 days   $18.99   pl_6e12a159e723
      1 year    $79.99   pl_458d0a58b52d
      lifetime  $170.00  pl_d57eca0307f7

  An UNRESOLVED payment creates no licence and returns 200 with a flag.
  Returning an error would make Bachs retry forever; creating a guessed licence
  would give someone the wrong access. Neither is acceptable, so it records the
  payment and tells you — with the exact env line that would fix it.

  ── SETUP ────────────────────────────────────────────────────────────────────
  Render environment:
      BACHS_WEBHOOK_SECRET = whsec_...        (already added)
      BACHS_EA_ID          = 1                (NOLIMITZ PRO — end users only
                                               ever buy this EA)
      BACHS_ADMIN_ID       = 2                (owning admin for created keys)

      BACHS_LINK_PLANS     = pl_28c868d8f88b:15days, pl_6e12a159e723:30days,
                             pl_458d0a58b52d:1year, pl_d57eca0307f7:lifetime
      BACHS_AMOUNT_PLANS   = 9.99:15days, 18.99:30days, 79.99:1year, 170:lifetime

  (BACHS_LINK_PLANS goes on ONE line — wrapped here only to fit.)

  Bachs dashboard webhook URL:
      https://nolimitz-backend-yfne.onrender.com/payments/bachs-webhook

  And in app/main.py:
      from app.routers import payments
      app.include_router(payments.router)

  DEPLOY ORDER: app/routers/bachs_plans.py must exist in the repo before or
  alongside this file. The import below is at module level, so a missing
  bachs_plans.py does not fail this route — it fails the whole application at
  startup.
================================================================================
"""

import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Admin, ExpertAdvisor, License
from app.routers.license import deliver_license, generate_license_key
from app.routers.bachs_plans import resolve_plan, config_summary

router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger("payments")

BACHS_WEBHOOK_SECRET = os.getenv("BACHS_WEBHOOK_SECRET", "")
BACHS_EA_ID = int(os.getenv("BACHS_EA_ID", "1"))
BACHS_ADMIN_ID = int(os.getenv("BACHS_ADMIN_ID", "2"))

# NOTE: the PLAN_DAYS dict that used to live here has been REMOVED, not moved.
# bachs_plans.PLAN_DAYS is the single authority for duration now. Two copies of
# the same mapping in two files is how one of them silently goes stale — which
# is exactly what happened to the $9.99 comment above.

# What each DURATION should cost, as a sanity check in the log only. Keyed by
# days rather than plan name, so it works whichever alias resolved ("15days",
# "monthly", "1year"...) without needing an entry per alias.
#
# A mismatch is worth seeing — promo, price change, or something wrong — but it
# must never change the duration. The resolver is the authority.
PLAN_EXPECTED_USD = {
    15: 9.99,
    30: 18.99,
    365: 79.99,
    36500: 170.00,
}


def _candidate_signatures(raw_body: bytes, timestamp: Optional[str] = None) -> dict:
    """Every plausible HMAC scheme, labelled.

    THE TIMESTAMP IS THE POINT. Live requests carry x-bachs-signature AND
    x-bachs-timestamp, and no body-only hash ever matched. Providers that send
    a timestamp almost always sign the two together — "<ts>.<body>" is the
    common form — because signing the body alone lets an attacker replay a
    captured request forever.

    Every combination is computed because Bachs documents none of them: the
    delimiter, the order, hex vs base64, and whether the whsec_ prefix is part
    of the key. The matching one is logged by name, so once it appears in the
    log the rest can be deleted.
    """
    import base64

    secrets = [BACHS_WEBHOOK_SECRET]
    if BACHS_WEBHOOK_SECRET.startswith("whsec_"):
        # Some providers treat the prefix as a label, not part of the key.
        secrets.append(BACHS_WEBHOOK_SECRET[len("whsec_"):])

    payloads = {"body": raw_body}
    if timestamp:
        ts = str(timestamp).encode()
        payloads["ts.body"] = ts + b"." + raw_body
        payloads["ts:body"] = ts + b":" + raw_body
        payloads["tsbody"] = ts + raw_body
        payloads["body.ts"] = raw_body + b"." + ts
        payloads["bodyts"] = raw_body + ts

    out = {}
    for idx, secret in enumerate(secrets):
        stag = "wp" if idx == 0 else "np"     # with / no whsec_ prefix
        keys = {"str": secret.encode()}
        try:
            decoded = base64.b64decode(secret + "=" * (-len(secret) % 4))
            if decoded:
                keys["b64key"] = decoded
        except Exception:
            pass

        for ktag, key in keys.items():
            for ptag, payload in payloads.items():
                mac = hmac.new(key, payload, hashlib.sha256)
                out[f"{stag}_{ktag}_{ptag}_hex"] = mac.hexdigest()
                out[f"{stag}_{ktag}_{ptag}_b64"] = base64.b64encode(
                    mac.digest()).decode()

    return out


def verify_signature(raw_body: bytes, signature: Optional[str],
                     timestamp: Optional[str] = None) -> bool:
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
        logger.warning("no signature header found on request")
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

    variants = _candidate_signatures(raw_body, timestamp)

    for name, expected in variants.items():
        for got in received:
            if hmac.compare_digest(expected, got):
                logger.info("signature verified using variant: %s", name)
                return True

    if os.getenv("BACHS_DEBUG_SIG", "").lower() == "true":
        logger.error("SIGNATURE MISMATCH — header=%r  ts=%r  body_len=%d  "
                     "(%d variants tried)",
                     signature[:80], timestamp, len(raw_body), len(variants))
        # Only the hex variants are printed: there are too many to log in full
        # and the received value is hex, so base64 forms cannot match anyway.
        for name, expected in variants.items():
            if name.endswith("_hex"):
                logger.error("   computed %-28s -> %s...", name, expected[:20])
        for got in received:
            logger.error("   received %s...", got[:20])

    return False


def _find_timestamp_header(request: Request) -> Optional[str]:
    """The timestamp that goes into the signed payload.

    x-bachs-timestamp on live requests, with x-syncpay-timestamp carrying the
    same value (Bachs runs on SyncPay). Matched by suffix so either works, and
    so a rename does not break it.
    """
    for key, value in request.headers.items():
        low = key.lower()
        if low.endswith("-timestamp") or low in ("timestamp", "x-timestamp"):
            return value
    return None


def _find_signature_header(request: Request, declared: Optional[str]) -> tuple:
    """Locate the signature header, whatever Bachs calls it.

    The declared Bachs-Signature parameter came back EMPTY on live retries
    while the request itself arrived fine, which means the header being sent is
    not the one documented. Rather than keep guessing names one deploy at a
    time, this scans for anything signature-shaped and reports what it used.

    Returns (value, header_name_used).
    """
    if declared:
        return declared, "Bachs-Signature"

    for key, value in request.headers.items():
        low = key.lower()
        if "signature" in low or low.endswith("-sig") or low.endswith("-hmac"):
            return value, key

    return None, None


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

    if os.getenv("BACHS_DEBUG_SIG", "").lower() == "true":
        # Every header, verbatim. "no Bachs-Signature header on request" told
        # us the expected name is wrong but not what the right one is, and
        # without seeing the actual request there is nothing to reason from.
        # Turn this off once the header name is known — headers can carry
        # credentials and this puts them in the log.
        logger.error("INBOUND HEADERS: %s", dict(request.headers))

    sig, header_used = _find_signature_header(request, bachs_signature)
    ts = _find_timestamp_header(request)
    if header_used and header_used != "Bachs-Signature":
        logger.info("signature taken from header %r (not Bachs-Signature)",
                    header_used)

    if not verify_signature(raw, sig, ts):
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
    #
    # LIMIT: this only sees licences created BY THIS WEBHOOK. A key you issued
    # by hand carries no bachs_charge_id, so pressing Retry on a payment you
    # already served manually WILL mint a second licence. The check guards
    # against Bachs retrying itself; it cannot know about work done outside the
    # system.
    if charge_id:
        # Raw SQL with an explicit ::jsonb cast. The ORM form
        # License.branding_snapshot["bachs_charge_id"].astext only compiles
        # against a JSONB column; this one is plain JSON, so it raised
        # AttributeError at request time — a 500 on a real payment. Casting in
        # SQL works for either column type and cannot break if the type
        # changes later.
        existing = db.execute(text(
            "select id, license_key from licenses "
            "where branding_snapshot::jsonb ->> 'bachs_charge_id' = :cid "
            "limit 1"
        ), {"cid": str(charge_id)}).first()

        if existing:
            logger.info("charge %s already issued licence %s — not duplicating",
                        charge_id, existing.id)
            return {"received": True, "duplicate": True,
                    "license_key": existing.license_key}

    # ---- WHICH PLAN DID THEY BUY? -------------------------------------------
    # Delegated. resolve_plan returns (plan, days, how) and has already logged
    # the link id, the amount, the full metadata and the exact env line that
    # would map this payment, if it could not decide.
    plan_id, days, how = resolve_plan(data, meta)
    if days is None:
        # Do NOT guess. A wrong expiry is worse than a manual key: the customer
        # either loses access early or gets more than they paid for, and
        # neither is visible until they complain.
        return {"received": True, "error": "could not determine plan",
                "action_required": "issue this licence manually"}

    logger.info("charge %s resolved to plan %s (%s days) via %s",
                charge_id, plan_id, days, how)

    # Price sanity check — logged, never acted on.
    try:
        expected = PLAN_EXPECTED_USD.get(days)
        if expected and settlement and abs(float(settlement) - expected) > 0.5:
            logger.warning("charge %s: plan %s (%s days) settled at %s, "
                           "expected ~%s (promo link or price change?)",
                           charge_id, plan_id, days, settlement, expected)
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
            # WHICH RULE decided the duration. If a customer ever disputes
            # their expiry, this says whether it came from checkout, from a
            # mapped link, or from the price — without re-reading the logs.
            "bachs_resolved_by": how,
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
        "resolved_by": how,
        "emailed": sent,
    }


@router.get("/bachs-webhook")
def bachs_webhook_health():
    """So a browser hit tells you the route exists — and what it is configured
    with.

    The 404s in the Bachs dashboard were the whole problem; being able to check
    the path is live in one click is worth the four lines.

    config_summary() adds the live link and amount maps, so you can confirm an
    env var actually took effect without making a real payment to find out.
    """
    return {
        "status": "ready",
        "secret_configured": bool(BACHS_WEBHOOK_SECRET),
        "ea_id": BACHS_EA_ID,
        **config_summary(),
    }
