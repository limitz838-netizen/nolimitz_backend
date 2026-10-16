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
from app.routers.license import deliver_license, generate_license_key, send_email
from app.routers.bachs_plans import resolve_plan, config_summary, TELEGRAM_PLANS

router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger("payments")

BACHS_WEBHOOK_SECRET = os.getenv("BACHS_WEBHOOK_SECRET", "")
BACHS_EA_ID = int(os.getenv("BACHS_EA_ID", "1"))
BACHS_ADMIN_ID = int(os.getenv("BACHS_ADMIN_ID", "2"))

# ---- Telegram Copier subscription ($11.99 / month) ---------------------------
# Each payment adds 30 days to the subscriber's ONE Telegram licence (found by
# email), plus a grace period so a late monthly payment does not cut copying
# mid-trade. The customer never sees a key: the app finds the licence by the
# email they are signed in with (GET /payments/telegram-subscription).
TELEGRAM_GRACE_DAYS = int(os.getenv("TELEGRAM_SUB_GRACE_DAYS", "2"))
TELEGRAM_SUB_PRICE = float(os.getenv("TELEGRAM_SUB_PRICE", "11.99"))
# The MT5 / Telegram Copier app. NOT app.nolimitzbots.co.ke -- that host serves
# only the Deriv bots, and the first subscription email sent customers there.
# A separate variable so an old APP_URL value in Render cannot override it.
APP_URL = os.getenv("TELEGRAM_APP_URL", "https://nolimitzbots.co.ke").rstrip("/")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")

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
            "   or coalesce(branding_snapshot::jsonb -> 'bachs_charge_ids', '[]'::jsonb) "
            "      @> to_jsonb(cast(:cid as text)) "
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

    if plan_id in TELEGRAM_PLANS:
        return _telegram_subscription_paid(
            db, email=email, name=name, charge_id=charge_id, event_id=event_id,
            plan_id=plan_id, days=days, settlement=settlement, how=how)

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


# ==============================================================================
# TELEGRAM COPIER SUBSCRIPTION
# ==============================================================================
def _naive_utc(d):
    if d is None:
        return None
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


def _telegram_licence_for(db: Session, email: str):
    row = db.execute(text("""
        select id from licenses
         where lower(client_email) = lower(:e)
           and branding_snapshot::jsonb ->> 'product' in ('telegram_copier', 'telegram_upgraded')
         order by id desc limit 1"""), {"e": email}).first()
    return db.query(License).filter(License.id == row.id).first() if row else None


# ------------------------------------------------------------------------------
# UPGRADE: a Telegram Copier subscriber enters an AI Trader licence key.
#
# The subscriber's OWN licence is upgraded in place rather than switching them
# to the new key. Their MT5 account, Telegram session, channels, symbol
# settings and any open Telegram trades all hang off this licence's id, so
# moving to another licence would strand them (and open Telegram trades would
# lose their closes). The purchased key is retired so it cannot be used twice.
#
#   product          telegram_copier  ->  telegram_upgraded
#                    (the copier lanes only fence off 'telegram_copier', so an
#                     upgraded licence gets master and AI trades again)
#   bachs_plan_id    telegram         ->  the purchased plan (30days, 1year...)
#   expires_at       the later of the two -- AI Trader and Telegram Copier now
#                    end on the same day
#
# Telegram payments made while upgraded are banked as tg_credit_days and turn
# into Telegram-only time when the upgraded period ends (_settle_upgrade).
# ------------------------------------------------------------------------------
def _product(lic) -> str:
    snap = lic.branding_snapshot if isinstance(lic.branding_snapshot, dict) else {}
    return str(snap.get("product") or "")


def _to_telegram_only(lic, now):
    snap = dict(lic.branding_snapshot or {})
    snap.update({"product": "telegram_copier", "bachs_plan_id": "telegram",
                 "downgraded_at": now.isoformat()})
    lic.branding_snapshot = snap
    lic.ai_enabled = False


def _settle_upgrade(db: Session, lic) -> bool:
    """An upgraded licence whose period has ended: if Telegram payments were
    banked during it, it carries on as Telegram-only for that many days.
    Returns True if it changed anything."""
    if lic is None or _product(lic) != "telegram_upgraded":
        return False
    now = datetime.utcnow()
    exp = _naive_utc(lic.expires_at)
    if exp is None or exp > now:
        return False
    snap = dict(lic.branding_snapshot or {})
    credit = int(snap.get("tg_credit_days") or 0)
    if credit <= 0:
        return False
    _to_telegram_only(lic, now)
    snap = dict(lic.branding_snapshot)
    snap["tg_credit_days"] = 0
    lic.branding_snapshot = snap
    lic.expires_at = (now + timedelta(days=credit)
                      + timedelta(days=TELEGRAM_GRACE_DAYS)).replace(tzinfo=timezone.utc)
    lic.is_active = True
    db.commit()
    db.refresh(lic)
    logger.info("upgraded licence %s ended — %s banked Telegram day(s) now running",
                lic.id, credit)
    return True


def _confirm_app_account(email: str):
    """Ask the app to confirm this email's account, so a subscriber who never
    clicked the sign-up confirmation can still log in straight away. Best
    effort: a failure here never affects the payment."""
    url = os.getenv("APP_CONFIRM_URL",
                    "https://mtbdwsarjwlshogszohb.supabase.co/functions/v1/telegram-paid-confirm")
    if not url or not WORKER_TOKEN:
        return
    try:
        import requests
        requests.post(url, json={"email": email},
                      headers={"X-Worker-Token": WORKER_TOKEN}, timeout=6)
    except Exception as e:
        logger.warning("app account confirm call failed for %s: %s", email, e)


def _telegram_subscription_paid(db: Session, *, email, name, charge_id, event_id,
                                plan_id, days, settlement, how):
    """A Telegram Copier payment: extend the subscriber's licence, or create it.

    Renewal arithmetic: the new period starts at the end of the PAID period
    (expiry minus grace) or now, whichever is later, so paying early never
    loses days and paying late never back-dates. Grace is added on top.
    """
    try:
        if settlement and abs(float(settlement) - TELEGRAM_SUB_PRICE) > 0.5:
            logger.warning("telegram charge %s settled at %s, expected ~%s",
                           charge_id, settlement, TELEGRAM_SUB_PRICE)
    except Exception:
        pass

    now = datetime.utcnow()
    grace = timedelta(days=TELEGRAM_GRACE_DAYS)
    cid = str(charge_id) if charge_id else None
    lic = _telegram_licence_for(db, email)
    banked = False
    _settle_upgrade(db, lic)

    if lic and _product(lic) == "telegram_upgraded":
        exp = _naive_utc(lic.expires_at)
        snap = dict(lic.branding_snapshot or {})
        ids = list(snap.get("bachs_charge_ids") or [])
        if cid and cid not in ids:
            ids.append(cid)
        snap.update({"bachs_charge_ids": ids[-60:], "last_charge_id": cid,
                     "last_event_id": event_id, "bachs_resolved_by": how})
        if exp and exp > now:
            # Already covered by the AI Trader licence: bank the days, they
            # become Telegram-only time when that licence period ends.
            snap["tg_credit_days"] = int(snap.get("tg_credit_days") or 0) + int(days)
            lic.branding_snapshot = snap
            db.commit()
            db.refresh(lic)
            banked = True
        else:
            # Upgrade period over and nothing banked: back to Telegram-only.
            lic.branding_snapshot = snap
            _to_telegram_only(lic, now)
            db.commit()
            db.refresh(lic)

    if banked:
        renewed = True
    elif lic:
        exp = _naive_utc(lic.expires_at)
        base = max(now, exp - grace) if exp else now
        lic.expires_at = (base + timedelta(days=days) + grace).replace(tzinfo=timezone.utc)
        lic.is_active = True
        snap = dict(lic.branding_snapshot or {})
        ids = list(snap.get("bachs_charge_ids") or [])
        if cid and cid not in ids:
            ids.append(cid)
        snap.update({"bachs_charge_ids": ids[-60:], "last_charge_id": cid,
                     "last_event_id": event_id, "bachs_settlement": str(settlement) if settlement else None,
                     "renewed_at": now.isoformat(), "bachs_resolved_by": how})
        lic.branding_snapshot = snap
        db.commit()
        db.refresh(lic)
        renewed = True
    else:
        ea = db.query(ExpertAdvisor).filter(ExpertAdvisor.id == BACHS_EA_ID).first()
        if not ea:
            logger.error("BACHS_EA_ID %s not found — cannot issue telegram licence", BACHS_EA_ID)
            raise HTTPException(status_code=500, detail="Configured EA not found")
        admin = db.query(Admin).filter(Admin.id == BACHS_ADMIN_ID).first()
        lic = License(
            admin_id=BACHS_ADMIN_ID,
            ea_id=ea.id,
            license_key=generate_license_key(db),
            client_name=name,
            client_email=email,
            mode_type="both",
            expires_at=(now + timedelta(days=days) + grace).replace(tzinfo=timezone.utc),
            is_active=True,
            branding_snapshot={
                "admin_code": getattr(admin, "admin_code", None),
                "display_name": "NolimitzBots",
                "product": "telegram_copier",
                "bachs_plan_id": "telegram",
                "bachs_charge_id": cid,
                "bachs_charge_ids": [cid] if cid else [],
                "bachs_event_id": event_id,
                "bachs_settlement": str(settlement) if settlement else None,
                "bachs_resolved_by": how,
                "source": "bachs",
            },
            ai_enabled=False,
            mt5_enabled=True,
            auto_trade_enabled=True,
            # On, so channel trades flow from the first signal. The app's Stop
            # button still turns it off, and the lanes respect that.
            execution_enabled=True,
        )
        db.add(lic)
        db.commit()
        db.refresh(lic)
        renewed = False

    until = _naive_utc(lic.expires_at)
    logger.info("telegram subscription %s for %s — licence %s, active until %s, charge %s",
                "RENEWED" if renewed else "STARTED", email, lic.id, until, charge_id)

    _confirm_app_account(email)

    # No key in the email: the app finds the subscription by this address.
    try:
        if banked:
            credit = int((lic.branding_snapshot or {}).get("tg_credit_days") or 0)
            sent, detail = send_email(
                to=email,
                subject="Telegram Copier payment received",
                html=("<p>Thank you%s. Your AI Trader licence already includes the "
                      "Telegram Copier until <b>%s UTC</b>, so this payment has been "
                      "saved: you have <b>%s day(s)</b> of Telegram Copier waiting after "
                      "that date.</p><p>If you plan to keep the AI Trader licence, you can "
                      "cancel the monthly Telegram Copier subscription.</p>")
                     % ((" " + name) if name else "", until.strftime("%d %b %Y"), credit),
            )
        else:
            sent, detail = send_email(
                to=email,
                subject="Your Telegram Copier is active - how to start" if not renewed
                        else "Your Telegram Copier subscription is renewed",
                html=telegram_welcome_html(name=name, email=email, until=until,
                                           renewed=renewed),
            )
        if not sent:
            logger.error("telegram subscription email failed for %s: %s", email, detail)
    except Exception as e:
        sent = False
        logger.error("telegram subscription email error for %s: %s", email, e)

    return {"received": True, "product": "telegram_copier", "license_id": lic.id,
            "renewed": renewed, "active_until": until.isoformat() if until else None,
            "plan": plan_id, "resolved_by": how, "emailed": sent}


def telegram_welcome_html(*, name, email, until, renewed=False, intro=""):
    """The how-to-get-in email for a Telegram Copier subscriber. No key: the
    app finds the subscription by the email they log in with."""
    from html import escape
    hi = escape(" " + name) if name else ""
    until_txt = until.strftime("%d %b %Y") if until else "-"
    login = APP_URL + "/login"
    signup = APP_URL + "/signup"
    step = ('<tr><td style="vertical-align:top;padding:6px 10px 6px 0;'
            'font-weight:bold;color:#b8860b">%d.</td>'
            '<td style="padding:6px 0">%s</td></tr>')
    steps = [
        ("<b>Log in with this exact email: %s</b><br>"
         "Go to <a href=\"%s\">%s</a>. No account yet? "
         "<a href=\"%s\">Create a free account</a> with this same email. "
         "If you signed up with email and password, open the confirmation "
         "email we sent you first (check Spam / Promotions) - you cannot log in "
         "until it is confirmed.") % (escape(email), login, login, signup),
        "Your dashboard opens on the <b>Telegram Copier</b>. No licence key is "
        "needed - your subscription is found from your email.",
        "Tap <b>MetaTrader 5</b> at the bottom and connect your MT5 account "
        "(login, password, server). One MT5 account is included.",
        "Back on <b>Telegram</b>, connect your Telegram account and add up to "
        "<b>3 channels</b> you already follow.",
        "In <b>MetaTrader 5</b>, turn on each symbol you want copied (for "
        "example XAUUSD) and give it a <b>lot size</b>. Symbols that are off or "
        "have no lot size are skipped.",
        "New channels start in <b>SHADOW</b> mode: signals show in your feed "
        "but no trades are placed. When the feed looks right, switch the "
        "channel to <b>LIVE</b>.",
    ]
    rows = "".join(step % (n, t) for n, t in enumerate(steps, 1))
    head = ("Your NolimitzBots Telegram Copier subscription is renewed."
            if renewed else
            "Thank you - your NolimitzBots Telegram Copier is active.")
    return (
        '<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.5;'
        'color:#222;max-width:560px">'
        "<p>Hi%s,</p>%s<p>%s Active until <b>%s</b>.</p>"
        "<p><b>How to start copying:</b></p>"
        '<table style="border-collapse:collapse">%s</table>'
        '<p style="margin:22px 0"><a href="%s" style="background:#c9a227;'
        "color:#111;padding:12px 22px;border-radius:8px;text-decoration:none;"
        'font-weight:bold">Log in to NolimitzBots</a></p>'
        '<p style="font-size:13px;color:#666">Your plan copies your own Telegram '
        "channels to one MT5 account. It does not include the AI robot or master "
        "account trades. Trading carries risk - use lot sizes your account can "
        "carry. Need help? Use the WhatsApp button on our website.</p>"
        "</div>"
    ) % (hi, intro, head, until_txt, rows, login)


@router.get("/telegram-subscription")
def telegram_subscription_lookup(
    email: str,
    x_worker_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """For the app's server function ONLY (worker token): does this email hold
    a Telegram Copier subscription, and is it active? Returns the licence key so
    the app can bind it to the signed-in account -- the customer never types it.
    """
    if not WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="WORKER_TOKEN is not configured")
    if not x_worker_token or not hmac.compare_digest(x_worker_token, WORKER_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid worker token")
    email = (email or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="email required")
    lic = _telegram_licence_for(db, email)
    if not lic:
        return {"found": False}
    _settle_upgrade(db, lic)
    until = _naive_utc(lic.expires_at)
    if until is None and _product(lic) == "telegram_upgraded":
        until = datetime(2126, 1, 1)          # lifetime key: no end date
    active = bool(lic.is_active) and until is not None and until > datetime.utcnow()
    upgraded = _product(lic) == "telegram_upgraded"
    snap = lic.branding_snapshot if isinstance(lic.branding_snapshot, dict) else {}
    return {"found": True, "active": active, "license_key": lic.license_key,
            "expires_at": until.isoformat() + "Z" if until else None,
            # True while this subscriber has also bought the AI Trader: the app
            # then shows the full dashboard on this same licence.
            "upgraded": upgraded,
            "plan": str(snap.get("bachs_plan_id") or "telegram") if upgraded else "telegram",
            "tg_credit_days": int(snap.get("tg_credit_days") or 0),
            "max_sources": 3, "max_mt5_accounts": 1}


def _require_worker(x_worker_token):
    if not WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="WORKER_TOKEN is not configured")
    if not x_worker_token or not hmac.compare_digest(x_worker_token, WORKER_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid worker token")


@router.post("/telegram-upgrade")
def telegram_upgrade(
    payload: dict,
    x_worker_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """For the app's server function ONLY (worker token). A Telegram Copier
    subscriber enters a purchased AI Trader key: upgrade THEIR licence in place
    and retire the key. The app has already checked the user is signed in as
    `email` and that no other account holds the key."""
    _require_worker(x_worker_token)
    email = str((payload or {}).get("email") or "").strip()
    key = str((payload or {}).get("license_key") or "").strip().upper()
    if not email or "@" not in email or not key:
        raise HTTPException(status_code=400, detail="email and license_key required")

    tg = _telegram_licence_for(db, email)
    if not tg:
        raise HTTPException(status_code=404,
                            detail="No Telegram Copier subscription found for this email.")
    _settle_upgrade(db, tg)

    bought = (db.query(License)
                .filter(text("upper(trim(license_key)) = :k")).params(k=key).first())
    if not bought:
        raise HTTPException(status_code=404,
                            detail="Licence key not found. Check the key and try again.")
    if bought.id == tg.id:
        raise HTTPException(status_code=400,
                            detail="That is your Telegram Copier key, not an AI Trader key.")
    bsnap = bought.branding_snapshot if isinstance(bought.branding_snapshot, dict) else {}
    if bsnap.get("product") in ("telegram_copier", "telegram_upgraded") \
            or bsnap.get("merged_into_license_id"):
        raise HTTPException(status_code=400,
                            detail="This key cannot be used for an upgrade.")
    now = datetime.utcnow()
    bexp = _naive_utc(bought.expires_at)
    if not bought.is_active:
        raise HTTPException(status_code=403, detail="This licence key is inactive.")
    if bexp is not None and bexp <= now:
        raise HTTPException(status_code=403, detail="This licence key has expired.")
    in_use = db.execute(text(
        "select count(*) from client_mt5_accounts where license_id = :i"),
        {"i": bought.id}).scalar() or 0
    if in_use or bought.activated_device_id:
        raise HTTPException(status_code=409,
                            detail="This key is already in use on another account. "
                                   "Contact support if this is your key.")

    # Which package was bought -- the copier's own reading of a licence, so
    # scanner limits, MT5 accounts and channels follow the purchased plan.
    from app.routers.telegram_user_copier import _plan_name
    plan = _plan_name(bought)

    texp = _naive_utc(tg.expires_at)
    snap = dict(tg.branding_snapshot or {})
    credit = int(snap.get("tg_credit_days") or 0)
    if bexp is None:                       # lifetime key
        new_exp = None
    elif _product(tg) == "telegram_upgraded":
        # A second key on an already-upgraded licence extends it.
        new_exp = max(d for d in (texp, bexp) if d is not None)
    elif texp is not None and texp > bexp:
        # Telegram has more time left than the key: the AI Trader ends with the
        # key, and the remaining Telegram days carry on after it.
        new_exp = bexp
        credit += int(-(-(texp - bexp).total_seconds() // 86400))   # round up
    else:
        new_exp = bexp
    snap["tg_credit_days"] = credit
    history = list(snap.get("upgrades") or [])
    history.append({"from_license_id": bought.id, "plan": plan,
                    "at": now.isoformat(),
                    "tg_expires_before": texp.isoformat() if texp else None})
    snap.update({"product": "telegram_upgraded", "bachs_plan_id": plan,
                 "upgraded_at": now.isoformat(), "upgrades": history[-20:]})
    tg.branding_snapshot = snap
    tg.expires_at = new_exp.replace(tzinfo=timezone.utc) if new_exp else None
    tg.is_active = True
    tg.ai_enabled = True
    tg.mt5_enabled = True
    if getattr(bought, "mode_type", None):
        tg.mode_type = bought.mode_type

    bsnap = dict(bsnap)
    bsnap.update({"merged_into_license_id": tg.id, "merged_at": now.isoformat()})
    bought.branding_snapshot = bsnap
    bought.is_active = False
    db.commit()
    db.refresh(tg)
    logger.info("telegram licence %s upgraded with key licence %s (%s) — now until %s",
                tg.id, bought.id, plan, _naive_utc(tg.expires_at))
    until = _naive_utc(tg.expires_at)
    return {"upgraded": True, "license_key": tg.license_key, "plan": plan,
            "expires_at": until.isoformat() + "Z" if until else None}


@router.post("/telegram-upgrade-sweep")
def telegram_upgrade_sweep(
    x_worker_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Scheduler only (worker token): upgraded licences whose period has ended
    and that have banked Telegram days switch to Telegram-only time."""
    _require_worker(x_worker_token)
    ids = [r.id for r in db.execute(text("""
        select id from licenses
         where branding_snapshot::jsonb ->> 'product' = 'telegram_upgraded'
           and expires_at <= now()
           and coalesce((branding_snapshot::jsonb ->> 'tg_credit_days')::int, 0) > 0
    """)).fetchall()]
    changed = 0
    for lid in ids:
        lic = db.query(License).filter(License.id == lid).first()
        if _settle_upgrade(db, lic):
            changed += 1
    return {"checked": len(ids), "switched_to_telegram": changed}


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
