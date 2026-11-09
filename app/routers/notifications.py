"""In-app notifications for users (step 66, Nov 2026).

Replaces the per-trade AI Desk emails, which were using up the daily Resend quota. Licence and
payment emails still go by email; trade news ("AI Desk opened...", "could not open...") now lands
in the dashboard bell instead.

  GET  /api/client/notifications?license_key=...          -> {"unread": n, "items": [...]}
  POST /api/client/notifications/read  {license_key, ids?} -> marks those (or all) as read

Old notifications are deleted after KEEP_DAYS so the table stays small.
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.database import Base, SessionLocal, get_db
from app.models import License

log = logging.getLogger(__name__)
KEEP_DAYS = int(os.getenv("NOTIFY_KEEP_DAYS", "30"))


class ClientNotification(Base):
    __tablename__ = "client_notifications"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, nullable=False, index=True)
    kind = Column(String, nullable=False, default="info")      # success | warning | info
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    read_at = Column(DateTime(timezone=True), nullable=True)


def notify(license_id: int, title: str, body: str = "", kind: str = "info", db: Optional[Session] = None) -> bool:
    """Store one notification. Never raises: a notification must never break trading."""
    own = db is None
    db = db or SessionLocal()
    try:
        db.add(ClientNotification(license_id=license_id, kind=kind, title=title[:200], body=body[:2000]))
        db.commit()
        return True
    except Exception as e:
        log.warning("notify licence %s failed: %s", license_id, e)
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        if own:
            db.close()


router = APIRouter(prefix="/api/client/notifications", tags=["Notifications"])


def _licence(db: Session, key: str) -> License:
    lic = db.query(License).filter(License.license_key == key).first()
    if not lic:
        raise HTTPException(status_code=400, detail="Invalid license key")
    return lic


@router.get("")
def list_notifications(license_key: str, limit: int = 30, db: Session = Depends(get_db)):
    lic = _licence(db, license_key)
    limit = max(1, min(limit, 100))
    base = db.query(ClientNotification).filter(ClientNotification.license_id == lic.id)
    # housekeeping, cheap and indexed
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    base.filter(ClientNotification.created_at < cutoff).delete(synchronize_session=False)
    db.commit()
    unread = base.filter(ClientNotification.read_at.is_(None)).count()
    rows = base.order_by(ClientNotification.id.desc()).limit(limit).all()
    return {"unread": unread, "items": [
        {"id": r.id, "kind": r.kind, "title": r.title, "body": r.body or "",
         "created_at": r.created_at.isoformat() if r.created_at else None,
         "read": r.read_at is not None} for r in rows]}


class ReadBody(BaseModel):
    license_key: str
    ids: Optional[List[int]] = None        # empty / missing = mark all as read


@router.post("/read")
def mark_read(data: ReadBody, db: Session = Depends(get_db)):
    lic = _licence(db, data.license_key)
    q = db.query(ClientNotification).filter(ClientNotification.license_id == lic.id,
                                            ClientNotification.read_at.is_(None))
    if data.ids:
        q = q.filter(ClientNotification.id.in_(data.ids))
    n = q.update({ClientNotification.read_at: datetime.now(timezone.utc)}, synchronize_session=False)
    db.commit()
    return {"marked": n}
