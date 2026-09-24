"""
================================================================================
  ENTITLEMENT CHECK  —  app/routers/entitlement.py
================================================================================

  THE PROBLEM THIS SOLVES

  Licences live in this Postgres. The premium/free flag on the dashboard lives
  in a different database (Supabase ai_profiles.is_premium). Nothing connected
  them, so a licence would expire here and the dashboard over there stayed
  premium forever.

  On 24 September that was 43 people on a lapsed licence still seeing the
  premium dashboard. They could not trade — expiry is enforced at validation,
  and none of them had checked in since expiring — but they were being shown
  something they were no longer paying for.

  This endpoint is the answer to one question, asked by whoever needs it:
  "is this person still entitled to premium?"

  ── WHY IT TAKES AN EMAIL AS WELL AS A KEY ───────────────────────────────────

  Because "their key expired" is NOT the same as "they stopped paying".

  Of those 43, one had already renewed. Their profile still pointed at the old
  key, because rebinding happens when they next open the dashboard, which they
  had not done. Demoting on key-expiry alone would have cut off a paying
  customer — the worst possible outcome of a cleanup job.

  So entitlement is: this key is live, OR any live licence exists on this email.

  ── FAIL CLOSED, NOT OPEN ────────────────────────────────────────────────────

  A key this backend has never heard of returns entitled=false with
  reason="key_unknown" — but callers must NOT demote on that. Unknown means we
  do not know, and "we do not know" is not grounds for taking away access
  someone may have paid for. Only reason="expired" is a safe demotion.

  That distinction is the whole safety model. It is spelled out in the response
  rather than left to the caller to infer.

  ── SETUP ────────────────────────────────────────────────────────────────────
  Needs WORKER_TOKEN, which is already set for the reminders job.

  In app/main.py:
      from app.routers import entitlement
      app.include_router(entitlement.router)
================================================================================
"""

import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/entitlement", tags=["Entitlement"])
logger = logging.getLogger("entitlement")

WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")

# One request covers this many accounts. Callers page beyond it. Keeps a single
# query bounded and predictable rather than letting someone post 50,000 keys.
MAX_BATCH = 500


def _require_worker(x_worker_token: Optional[str] = Header(None)):
    """Same token the reminders job uses.

    This endpoint reveals whether an email holds a licence, so it is not public.
    """
    if not WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="WORKER_TOKEN is not configured")
    if not x_worker_token or x_worker_token != WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid worker token")
    return True


class Account(BaseModel):
    key: Optional[str] = Field(None, description="The licence key on the profile")
    email: Optional[str] = Field(None, description="The account's email address")


class CheckRequest(BaseModel):
    accounts: List[Account]


@router.post("/check")
def check_entitlement(
    payload: CheckRequest,
    _: bool = Depends(_require_worker),
    db: Session = Depends(get_db),
):
    """For each account, say whether premium access is still deserved.

    Response per account:
        entitled       true  -> leave premium alone
        reason         why, in one word
        safe_to_demote true ONLY when we are certain they have lapsed

    Callers should act on safe_to_demote, never on entitled alone. The two
    differ exactly where we are unsure, and being unsure must never cost a
    customer their access.
    """
    accounts = payload.accounts or []
    if len(accounts) > MAX_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"{len(accounts)} accounts in one request; maximum is {MAX_BATCH}")

    keys = [a.key.strip() for a in accounts if a.key and a.key.strip()]
    emails = [a.email.strip().lower() for a in accounts if a.email and a.email.strip()]

    # ---- one query for every key we were asked about -----------------------
    key_rows = {}
    if keys:
        rows = db.execute(text("""
            select license_key,
                   bool_or(is_active and expires_at > now()) as live,
                   max(expires_at) as latest_expiry
            from licenses
            where license_key = any(:keys)
            group by license_key
        """), {"keys": keys}).mappings().all()
        key_rows = {r["license_key"]: r for r in rows}

    # ---- one query for every email, so renewals under a new key count ------
    email_live = set()
    if emails:
        rows = db.execute(text("""
            select distinct lower(trim(client_email)) as em
            from licenses
            where lower(trim(client_email)) = any(:emails)
              and is_active and expires_at > now()
        """), {"emails": emails}).mappings().all()
        email_live = {r["em"] for r in rows}

    results = []
    counts = {"entitled": 0, "expired": 0, "key_unknown": 0, "no_identifiers": 0}

    for a in accounts:
        key = (a.key or "").strip()
        email = (a.email or "").strip().lower()

        if not key and not email:
            counts["no_identifiers"] += 1
            results.append({"key": a.key, "email": a.email, "entitled": False,
                            "reason": "no_identifiers", "safe_to_demote": False})
            continue

        # Renewal under any key wins, and is checked FIRST. Someone who has paid
        # again must never be demoted because their profile still points at the
        # old key.
        if email and email in email_live:
            counts["entitled"] += 1
            results.append({"key": a.key, "email": a.email, "entitled": True,
                            "reason": "active_licence_on_email",
                            "safe_to_demote": False})
            continue

        row = key_rows.get(key) if key else None

        if row is None:
            # We have never seen this key. That is not evidence of lapsing — it
            # could be a key from elsewhere, a typo, or a deleted record. Report
            # it, demote nothing.
            counts["key_unknown"] += 1
            results.append({"key": a.key, "email": a.email, "entitled": False,
                            "reason": "key_unknown", "safe_to_demote": False})
            continue

        if row["live"]:
            counts["entitled"] += 1
            results.append({"key": a.key, "email": a.email, "entitled": True,
                            "reason": "key_active",
                            "expires_at": str(row["latest_expiry"]),
                            "safe_to_demote": False})
            continue

        # Key exists, is not live, and no other live licence on the email.
        # This is the only case we are certain about.
        counts["expired"] += 1
        results.append({"key": a.key, "email": a.email, "entitled": False,
                        "reason": "expired",
                        "expires_at": str(row["latest_expiry"]),
                        "safe_to_demote": True})

    logger.info("entitlement check: %s accounts -> %s", len(accounts), counts)
    return {"checked": len(accounts), "summary": counts, "results": results}


@router.get("/lapsed-summary")
def lapsed_summary(
    _: bool = Depends(_require_worker),
    db: Session = Depends(get_db),
):
    """Counts only — no personal data. For a quick 'is this drifting again?'.

    If lapsed_but_flagged climbs back up over time, the sync job has stopped
    running and nobody would otherwise notice until a customer mentioned it.
    """
    row = db.execute(text("""
        select
          count(*) filter (where is_active and expires_at > now()) as live_licences,
          count(*) filter (where is_active and expires_at <= now()) as expired_but_active,
          count(*) filter (where is_active and expires_at <= now()
                             and execution_enabled) as expired_execution_flag_on
        from licenses
    """)).mappings().first()
    return dict(row)
