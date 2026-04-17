from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import MT5Worker
from app.schemas import MT5WorkerRegisterRequest, MT5WorkerResponse

router = APIRouter(prefix="/mt5-workers", tags=["MT5 Workers"])


def utc_now():
    return datetime.now(timezone.utc)


@router.post("/register", response_model=MT5WorkerResponse)
def register_mt5_worker(payload: MT5WorkerRegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(MT5Worker).filter(
        MT5Worker.worker_name == payload.worker_name.strip()
    ).first()

    if existing:
        existing.worker_type = payload.worker_type.strip()
        existing.terminal_path = payload.terminal_path.strip()
        existing.data_path = payload.data_path.strip() if payload.data_path else None
        existing.is_active = True
        existing.last_heartbeat = utc_now()
        existing.last_error = None

        db.commit()
        db.refresh(existing)
        return existing

    row = MT5Worker(
        worker_name=payload.worker_name.strip(),
        worker_type=payload.worker_type.strip(),
        terminal_path=payload.terminal_path.strip(),
        data_path=payload.data_path.strip() if payload.data_path else None,
        is_active=True,
        is_busy=False,
        last_heartbeat=utc_now(),
        last_error=None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/", response_model=list[MT5WorkerResponse])
def list_mt5_workers(db: Session = Depends(get_db)):
    return db.query(MT5Worker).order_by(MT5Worker.id.asc()).all()


@router.post("/{worker_name}/heartbeat", response_model=MT5WorkerResponse)
def heartbeat_mt5_worker(worker_name: str, db: Session = Depends(get_db)):
    row = db.query(MT5Worker).filter(
        MT5Worker.worker_name == worker_name.strip()
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="Worker not found")

    row.last_heartbeat = utc_now()
    row.is_active = True
    db.commit()
    db.refresh(row)
    return row