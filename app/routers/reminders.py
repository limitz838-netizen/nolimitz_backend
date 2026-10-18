"""
================================================================================
  EXPIRY REMINDERS  —  app/routers/reminders.py
================================================================================

  WHAT THIS DOES
  Emails a customer 5 days before their licence expires, so they renew instead
  of silently losing their copier and wondering why trades stopped.

  Right now: 235 active licences, 15 of them expiring within 5 days, all with
  valid email addresses, none of whom have renewed. Those 15 are the first run.

  ── WHO GETS ONE ─────────────────────────────────────────────────────────────

      is_active = true
      AND expires_at is in the future
      AND expires_at is within the next 5 days
      AND nobody has already renewed on that email
      AND we have not already sent this exact reminder

  There is deliberately NO filter on plan length. A lifetime licence expires in
  ~100 years, so it can never fall inside a 5-day window — the maths excludes it
  without needing to know what someone bought. A 1-year licence DOES match, on
  day 360, which is what you want: yearly customers renew too. If you truly only
  want the short plans, set REMINDER_MAX_PLAN_DAYS=30.

  ── WHY A WINDOW, NOT "EXACTLY 5 DAYS FROM NOW" ──────────────────────────────

  If the job asked for licences expiring on exactly day 5 and the cron missed a
  morning — Render restart, network blip, you paused the service — everyone due
  that day would NEVER be reminded. Nobody would notice until a customer asked
  why their key died.

  A window plus a sent-marker is self-healing: a missed day is picked up on the
  next run, and the marker stops anyone being emailed twice.

  ── DUPLICATE PROTECTION ─────────────────────────────────────────────────────

  A licence_reminders table with a UNIQUE constraint on
  (license_id, kind, for_expiry). The row is claimed BEFORE the email is sent,
  with ON CONFLICT DO NOTHING. If the insert returns nothing, someone else
  already has it and this run skips. If the send then fails, the marker is
  deleted so tomorrow retries.

  That makes double-sending impossible even if two runs overlap, or if you press
  the button twice, or if Render runs two instances. "We checked first" is not
  protection — the database constraint is.

  for_expiry is part of the key so that a licence whose expiry is EXTENDED gets
  a fresh reminder next time round, rather than being permanently marked done.

  ── SETUP ────────────────────────────────────────────────────────────────────
  Render environment (all optional except the token):

      WORKER_TOKEN            = <the same one the bridge already uses>
      REMINDER_DAYS_BEFORE    = 5        (default)
      REMINDER_MAX_PER_RUN    = 200      (safety cap)
      REMINDER_MAX_PLAN_DAYS  = 0        (0 = no limit; 30 = short plans only)

  In app/main.py:
      from app.routers import reminders
      app.include_router(reminders.router)

  RUN IT DRY FIRST. Before any cron, call it by hand with dry_run=true and read
  the list. It sends nothing and shows you exactly who would be emailed.
================================================================================
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.license import send_email

router = APIRouter(prefix="/reminders", tags=["Reminders"])
logger = logging.getLogger("reminders")

WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")
CLIENT_APP_URL = os.getenv("CLIENT_APP_URL", "https://nolimitzbots.co.ke")
PRICING_URL = os.getenv("PRICING_URL", "https://nolimitzbots.co.ke/pricing")

DAYS_BEFORE = int(os.getenv("REMINDER_DAYS_BEFORE", "5"))
MAX_PER_RUN = int(os.getenv("REMINDER_MAX_PER_RUN", "200"))
# 0 means no limit. Set to 30 to remind only 15- and 30-day customers.
MAX_PLAN_DAYS = int(os.getenv("REMINDER_MAX_PLAN_DAYS", "0"))

# Pause between sends. Resend rate-limits, and 200 emails fired in one burst is
# also a good way to look like a spammer to inbox providers.
SEND_PAUSE_SECONDS = float(os.getenv("REMINDER_SEND_PAUSE", "0.4"))

KIND_EXPIRY = "expiry_soon"


def _require_worker(x_worker_token: Optional[str] = Header(None)):
    """Same non-expiring token the MT5 bridge uses.

    This endpoint sends mail to every customer in the window. An open URL would
    let anyone on the internet mail your entire customer list, repeatedly.
    """
    if not WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="WORKER_TOKEN is not configured")
    if not x_worker_token or x_worker_token != WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid worker token")
    return True


def ensure_table(db: Session):
    """Create the dedupe table if it isn't there.

    Done in SQL rather than as a model + migration because this backend has no
    migration tool wired up, and a reminder job should not be the thing that
    introduces one at 4am. IF NOT EXISTS makes it safe to run on every call.
    """
    db.execute(text("""
        create table if not exists license_reminders (
            id          serial primary key,
            license_id  integer     not null,
            kind        varchar(40) not null,
            for_expiry  date        not null,
            sent_at     timestamptz not null default now(),
            constraint license_reminders_unique unique (license_id, kind, for_expiry)
        )
    """))
    db.commit()


def _select_due(db: Session, days_before: int, limit: int):
    """Licences inside the window that still need a reminder.

    The NOT EXISTS on licenses is the "already renewed" check: if the same email
    has another active licence expiring later, they have already paid again and
    must not be nagged. Emails are compared lower(trim(...)) because the same
    person appears as "Bob@x.com " and "bob@x.com".
    """
    plan_clause = ""
    if MAX_PLAN_DAYS > 0:
        plan_clause = (" and extract(epoch from (l.expires_at - l.created_at))/86400 "
                       f"<= {int(MAX_PLAN_DAYS)} + 1 ")

    sql = f"""
        select l.id, l.license_key, l.client_name, l.client_email,
               l.expires_at, l.expires_at::date as for_expiry,
               greatest(0, round(extract(epoch from (l.expires_at - now()))/86400)) as days_left
        from licenses l
        where l.is_active
          and l.expires_at > now()
          and l.expires_at <= now() + (:days || ' days')::interval
          and l.client_email is not null
          and position('@' in l.client_email) > 1
          {plan_clause}
          and not exists (
              select 1 from licenses l2
              where lower(trim(l2.client_email)) = lower(trim(l.client_email))
                and l2.is_active
                and l2.expires_at > l.expires_at
          )
          and not exists (
              select 1 from license_reminders r
              where r.license_id = l.id
                and r.kind = :kind
                and r.for_expiry = l.expires_at::date
          )
        order by l.expires_at asc
        limit :limit
    """
    return db.execute(text(sql), {
        "days": str(days_before), "kind": KIND_EXPIRY, "limit": limit,
    }).mappings().all()


def _claim(db: Session, license_id: int, for_expiry) -> bool:
    """Reserve this reminder. True if we got it, False if it was already taken.

    The unique constraint does the work. Claiming BEFORE sending means a crash
    mid-send leaves a marker and nobody gets a second copy; the alternative
    (send, then record) double-sends whenever the recording fails.
    """
    row = db.execute(text("""
        insert into license_reminders (license_id, kind, for_expiry)
        values (:lid, :kind, :fex)
        on conflict on constraint license_reminders_unique do nothing
        returning id
    """), {"lid": license_id, "kind": KIND_EXPIRY, "fex": for_expiry}).first()
    db.commit()
    return row is not None


def _release(db: Session, license_id: int, for_expiry):
    """Undo a claim when the email failed, so the next run tries again."""
    db.execute(text("""
        delete from license_reminders
        where license_id = :lid and kind = :kind and for_expiry = :fex
    """), {"lid": license_id, "kind": KIND_EXPIRY, "fex": for_expiry})
    db.commit()


def _clean_name(name: Optional[str]) -> Optional[str]:
    """Only greet by name when it IS a name.

    client_name is free text and a good number of rows hold the customer's own
    email address, because that is what got typed into the form. "Hi
    badouadjemanenourdine66@gmail.com," looks like a phishing mail. When the
    value contains @ or looks like an address, greet without a name instead.
    """
    if not name:
        return None
    n = name.strip()
    if not n or "@" in n:
        return None
    return n


def _when_phrase(days_left: int) -> str:
    """'today' / 'tomorrow' / 'in N days'.

    The window includes licences expiring today — two of the first fifteen do.
    They should still be told, but "expires in 0 days" is not English and reads
    as a broken system.
    """
    if days_left <= 0:
        return "today"
    if days_left == 1:
        return "tomorrow"
    return f"in {days_left} days"


def reminder_html(name: Optional[str], license_key: str, days_left: int,
                  expires_at) -> str:
    """The renewal email.

    Same plain styling as the licence delivery email. No images, no tracking
    pixel, no marketing markup — this has to reach the inbox, and heavy HTML is
    the quickest way into spam.

    It states plainly what happens when the key expires. A reminder that does
    not say "your trades will stop copying" is not a reminder, it is an advert.
    """
    clean = _clean_name(name)
    greeting = f"Hi {clean}," if clean else "Hi,"
    when = expires_at.strftime("%d %B %Y") if expires_at else "soon"
    phrase = _when_phrase(days_left)

    return f"""<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;
     max-width:520px;margin:0 auto;color:#111;line-height:1.6">
  <p>{greeting}</p>
  <p>Your NolimitzBots licence expires <strong>{phrase}</strong>, on {when}.</p>
  <div style="background:#fff8e6;border:1px solid #f0d998;border-radius:8px;
       padding:16px;margin:20px 0">
    <div style="font-size:12px;letter-spacing:1px;color:#8a6d1f;
         text-transform:uppercase;margin-bottom:6px">Your licence key</div>
    <div style="font-family:ui-monospace,Consolas,monospace;font-size:18px;
         font-weight:600">{license_key}</div>
  </div>
  <p>When it expires, trades stop copying to your MT5 account. Your settings,
  symbols and lot size are all kept — renewing switches everything back on with
  the same setup.</p>
  <p style="margin:26px 0">
    <a href="{PRICING_URL}"
       style="background:#111;color:#fff;padding:13px 26px;border-radius:8px;
              text-decoration:none;font-weight:600;display:inline-block">
      Renew my licence
    </a>
  </p>
  <p style="color:#555;font-size:14px">Renewing early does not lose you any
  days — a new key starts when you activate it.</p>
  <hr style="border:none;border-top:1px solid #eee;margin:26px 0">
  <p style="color:#888;font-size:12px">
    You are receiving this because you hold a NolimitzBots licence that is about
    to expire. <a href="{CLIENT_APP_URL}" style="color:#888">nolimitzbots.co.ke</a><br>
    Trading involves risk of loss. Past performance does not indicate future
    results.
  </p>
</div>"""


@router.post("/run-expiry")
def run_expiry_reminders(
    dry_run: bool = Query(False, description="Preview only — sends nothing"),
    days_before: Optional[int] = Query(None, description="Override the window"),
    _: bool = Depends(_require_worker),
    db: Session = Depends(get_db),
):
    """Send the 5-day expiry reminders. Safe to call repeatedly.

    Run it with dry_run=true first and read the list before letting a schedule
    near it.
    """
    ensure_table(db)
    window = days_before if days_before is not None else DAYS_BEFORE
    due = _select_due(db, window, MAX_PER_RUN)

    if dry_run:
        return {
            "dry_run": True,
            "window_days": window,
            "would_send": len(due),
            "recipients": [
                {"license_id": r["id"], "email": r["client_email"],
                 "name": r["client_name"], "days_left": int(r["days_left"]),
                 "expires_at": str(r["expires_at"])}
                for r in due
            ],
        }

    sent, failed, skipped = 0, 0, 0
    failures = []

    for r in due:
        if not _claim(db, r["id"], r["for_expiry"]):
            skipped += 1          # another run got there first
            continue

        days_left = int(r["days_left"])
        ok, detail = send_email(
            to=r["client_email"],
            subject=f"Your NolimitzBots licence expires {_when_phrase(days_left)}",
            html=reminder_html(r["client_name"], r["license_key"],
                               days_left, r["expires_at"]),
        )

        if ok:
            sent += 1
            logger.info("expiry reminder sent: licence %s -> %s (expires %s)",
                        r["id"], r["client_email"], _when_phrase(days_left))
        else:
            # Release the claim so the next run retries rather than silently
            # marking this customer as reminded.
            _release(db, r["id"], r["for_expiry"])
            failed += 1
            failures.append({"license_id": r["id"], "email": r["client_email"],
                             "error": detail})
            logger.error("expiry reminder FAILED: licence %s -> %s: %s",
                         r["id"], r["client_email"], detail)

        time.sleep(SEND_PAUSE_SECONDS)

    logger.info("expiry reminder run complete: sent=%s failed=%s skipped=%s",
                sent, failed, skipped)

    return {"dry_run": False, "window_days": window, "considered": len(due),
            "sent": sent, "failed": failed, "skipped_already_claimed": skipped,
            "failures": failures}


@router.get("/status")
def reminders_status(
    _: bool = Depends(_require_worker),
    db: Session = Depends(get_db),
):
    """What the job would do and what it has done. Sends nothing.

    Useful for checking the schedule is actually firing: if last_sent_at is
    three days old, the cron is not running, and you would otherwise only find
    out when a customer's key died without warning.
    """
    ensure_table(db)
    counts = db.execute(text("""
        select
          (select count(*) from licenses where is_active) as active_licences,
          (select count(*) from licenses
            where is_active and expires_at > now()
              and expires_at <= now() + (:days || ' days')::interval) as in_window,
          (select count(*) from license_reminders where kind = :kind) as reminders_sent,
          (select max(sent_at) from license_reminders where kind = :kind) as last_sent_at
    """), {"days": str(DAYS_BEFORE), "kind": KIND_EXPIRY}).mappings().first()

    return {
        "window_days": DAYS_BEFORE,
        "max_per_run": MAX_PER_RUN,
        "max_plan_days": MAX_PLAN_DAYS or "no limit",
        "active_licences": counts["active_licences"],
        "in_window_now": counts["in_window"],
        "reminders_sent_all_time": counts["reminders_sent"],
        "last_sent_at": str(counts["last_sent_at"]) if counts["last_sent_at"] else None,
    }
