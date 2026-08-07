"""
================================================================================
  WORKER ENDPOINTS  —  machine-to-machine only
================================================================================

  Every route here is called by a background worker, never by a browser, so all
  of them sit behind a shared secret in the X-Worker-Token header.

  ── WHY THIS FILE CHANGED ────────────────────────────────────────────────────
  1. /worker/pending-mt5 had NO authentication and returned mt_password in
     plaintext. Anyone who knew the URL could read every unverified client's
     MT5 login, password and server. The password field is now gone entirely
     and the route requires a token.
  2. /worker/update-mt5-status had no authentication either, so anyone could
     mark accounts verified or failed — silently switching clients off.
  3. Added /register and /{worker_name}/heartbeat so the master bridge and the
     other workers can report themselves, which is what drives the
     "MT5 worker online" indicator on the admin dashboard.

  ── SETUP ────────────────────────────────────────────────────────────────────
  Set WORKER_TOKEN to the same long random string in BOTH places:
      • Render → service → Environment
      • the VPS .env.local
  Generate one with:
      py -3.10 -c "import secrets; print(secrets.token_urlsafe(32))"
================================================================================
"""

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ClientMT5Account

try:
    from app.models import WorkerHeartbeat
except Exception:  # pragma: no cover - older schema
    WorkerHeartbeat = None


router = APIRouter(
    prefix="/worker",
    tags=["MT5 Workers"]
)

WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")

# A worker is treated as offline after three missed beats at the default
# 25-second interval.
WORKER_ONLINE_WINDOW_SEC = int(os.getenv("WORKER_ONLINE_WINDOW_SEC", "90"))


def utc_now():
    return datetime.now(timezone.utc)


def _aware(dt):
    """Some drivers hand back naive datetimes; normalise before comparing."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ==============================================================================
# AUTH
# ==============================================================================
def require_worker_token(x_worker_token: str = Header(None)):
    """Shared secret for machine callers.

    Fails closed: if WORKER_TOKEN is unset the routes refuse to serve rather
    than falling back to open access.
    """
    if not WORKER_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="WORKER_TOKEN is not configured on the server",
        )
    if x_worker_token != WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid worker token")
    return True


# ==============================================================================
# MT5 VERIFICATION QUEUE
# ==============================================================================
@router.get("/pending-mt5")
def get_pending_mt5_accounts(
    _: bool = Depends(require_worker_token),
    db: Session = Depends(get_db),
):
    """Accounts awaiting verification.

    NOTE: mt_password is deliberately NOT returned. The verification worker
    runs on the same host as the database session and reads credentials
    directly, so no password ever needs to cross the network. If some caller
    genuinely needs it, that caller should be reading the DB, not this route.
    """
    rows = db.query(ClientMT5Account).filter(
        ClientMT5Account.is_verified == False  # noqa: E712
    ).all()

    results = []
    for row in rows:
        results.append({
            "id": row.id,
            "license_id": row.license_id,
            "login": row.login,
            "server": row.server,
        })

    return {
        "count": len(results),
        "accounts": results,
    }


@router.post("/update-mt5-status")
def update_mt5_status(
    payload: dict,
    _: bool = Depends(require_worker_token),
    db: Session = Depends(get_db),
):
    row = db.query(ClientMT5Account).filter(
        ClientMT5Account.id == payload.get("id")
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="MT5 account not found")

    success = payload.get("success", False)

    row.last_verified_at = utc_now()

    if success:
        row.is_verified = True
        row.is_active = True
        row.verification_error = None

        row.account_name = payload.get("account_name")
        row.broker_name = payload.get("broker_name")
        row.balance = payload.get("balance")
        row.equity = payload.get("equity")

    else:
        row.is_verified = False
        row.is_active = False
        row.verification_error = payload.get(
            "error",
            "Verification failed"
        )

    db.commit()
    db.refresh(row)

    return {
        "message": "MT5 status updated",
        "verified": row.is_verified,
    }


# ==============================================================================
# WORKER REGISTRY
# ==============================================================================
@router.post("/register")
def register_worker(
    payload: dict,
    _: bool = Depends(require_worker_token),
    db: Session = Depends(get_db),
):
    """A worker announces itself. Idempotent — safe on every restart, and the
    bridge calls it whenever a heartbeat comes back 404."""
    if WorkerHeartbeat is None:
        raise HTTPException(status_code=503, detail="WorkerHeartbeat model unavailable")

    name = (payload.get("worker_name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="worker_name is required")

    row = db.query(WorkerHeartbeat).filter(
        WorkerHeartbeat.worker_name == name
    ).first()
    if not row:
        row = WorkerHeartbeat(worker_name=name)
        db.add(row)

    row.last_beat = utc_now()

    # These columns may not exist on every schema version — set only what does.
    for field, value in (
        ("worker_type", payload.get("worker_type")),
        ("status", payload.get("status") or "online"),
        ("host", payload.get("host")),
        ("terminal_path", payload.get("terminal_path")),
        ("detail", payload.get("worker_type") or "worker"),
    ):
        if hasattr(row, field) and value is not None:
            setattr(row, field, str(value)[:200])

    db.commit()

    return {
        "message": "registered",
        "worker_name": name,
        "last_beat": row.last_beat,
    }


@router.post("/{worker_name}/heartbeat")
def worker_heartbeat(
    worker_name: str,
    _: bool = Depends(require_worker_token),
    db: Session = Depends(get_db),
):
    """Called on a timer by each worker. A 404 tells the caller to register,
    which is exactly what the bridge does."""
    if WorkerHeartbeat is None:
        raise HTTPException(status_code=503, detail="WorkerHeartbeat model unavailable")

    row = db.query(WorkerHeartbeat).filter(
        WorkerHeartbeat.worker_name == worker_name
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="worker not registered")

    row.last_beat = utc_now()
    if hasattr(row, "status"):
        row.status = "online"
    db.commit()

    return {
        "message": "ok",
        "worker_name": worker_name,
        "last_beat": row.last_beat,
    }


@router.get("/status")
def workers_status(db: Session = Depends(get_db)):
    """Which workers are alive.

    Deliberately readable without the worker token so the admin dashboard can
    poll it. It exposes no credentials — only names and beat times.
    """
    if WorkerHeartbeat is None:
        return {"workers": [], "any_online": False}

    now = utc_now()
    workers = []

    for row in db.query(WorkerHeartbeat).all():
        beat = _aware(row.last_beat)
        age = (now - beat).total_seconds() if beat else None
        workers.append({
            "worker_name": row.worker_name,
            "last_beat": beat,
            "seconds_ago": int(age) if age is not None else None,
            "online": bool(age is not None and age < WORKER_ONLINE_WINDOW_SEC),
            "worker_type": getattr(row, "worker_type", None),
        })

    workers.sort(key=lambda w: (not w["online"], w["worker_name"]))

    return {
        "workers": workers,
        "any_online": any(w["online"] for w in workers),
        "bridge_online": any(
            w["online"] and str(w["worker_name"]).startswith("master-bridge")
            for w in workers
        ),
    }
