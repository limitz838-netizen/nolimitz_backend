"""
app/routers/bridge_heartbeat.py -- where the VPS master bridge says "I'm alive"

The bridge posts every 15 s to
    POST /worker/register                     (on start, and after any 404)
    POST /worker/{worker_name}/heartbeat
with the X-Worker-Token header. Those routes did not exist, so every call got
404 and the bridge never showed as online. This records each beat in the
existing worker_heartbeats table (the same table /api/client/engine-status
reads), so the master shows as online with its last-seen time.

Only accepts the shared WORKER_TOKEN. Stores nothing secret.
"""
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException

from app.database import SessionLocal
from app.models import WorkerHeartbeat

router = APIRouter(prefix="/worker", tags=["Worker heartbeat"])

WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")
_NAME_OK = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def _check(token):
    if not WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="WORKER_TOKEN is not configured")
    if token != WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid worker token")


def _beat(name: str, detail: str):
    if not _NAME_OK.match(name or ""):
        raise HTTPException(status_code=400, detail="Bad worker name")
    db = SessionLocal()
    try:
        row = db.query(WorkerHeartbeat).filter(WorkerHeartbeat.worker_name == name).first()
        if row is None:
            row = WorkerHeartbeat(worker_name=name)
            db.add(row)
        row.detail = detail[:200]
        row.last_beat = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


@router.post("/register")
def register(payload: dict, x_worker_token: str = Header(None)):
    _check(x_worker_token)
    name = str(payload.get("worker_name") or "")
    kind = str(payload.get("worker_type") or "worker")[:40]
    status = str(payload.get("status") or "online")[:20]
    _beat(name, f"{kind} {status}")
    return {"ok": True, "worker_name": name}


@router.post("/{worker_name}/heartbeat")
def heartbeat(worker_name: str, x_worker_token: str = Header(None)):
    _check(x_worker_token)
    _beat(worker_name, "master-bridge online")
    return {"ok": True}
