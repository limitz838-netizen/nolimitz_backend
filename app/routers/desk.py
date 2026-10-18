"""
app/routers/desk.py -- read-only API for the Desk page (Oct 2026)

  GET /api/desk/state?worker=desk-1&after_id=<last event id seen>

Returns everything the Desk page draws in one call, so the page can poll it
every few seconds: the worker's latest snapshot (balance, equity, positions,
candles, agent status lines), new events since after_id, recent trades and a
running summary.

Read-only and public on purpose: it shows the Desk demo account's results,
never credentials. The worker masks the MT5 login before writing the snapshot.
Nothing in this router can place, change or close a trade.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.desk.models import DeskEvent, DeskState, DeskTrade

router = APIRouter(prefix="/api/desk", tags=["desk"])

STALE_AFTER_SEC = 60


def _trade_json(t: DeskTrade) -> dict:
    return {
        "id": t.id, "ticket": t.ticket, "symbol": t.symbol, "direction": t.direction,
        "lot": t.lot, "entry": t.entry_price, "stop": t.current_stop, "initial_stop": t.initial_stop,
        "target": t.target, "opened_at": t.opened_at.isoformat() if t.opened_at else None,
        "is_open": t.is_open, "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "exit": t.exit_price, "profit": t.profit_usd, "r": t.r_multiple, "reason": t.exit_reason,
        "is_demo": t.is_demo,
    }


@router.get("/state")
def desk_state(
    worker: str = Query("desk-1", max_length=64),
    after_id: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    row = db.query(DeskState).filter(DeskState.worker == worker).first()
    now = datetime.now(timezone.utc)
    age = None
    if row and row.updated_at:
        ua = row.updated_at if row.updated_at.tzinfo else row.updated_at.replace(tzinfo=timezone.utc)
        age = (now - ua).total_seconds()

    q = db.query(DeskEvent).filter(DeskEvent.worker == worker)
    if after_id:
        events = q.filter(DeskEvent.id > after_id).order_by(DeskEvent.id.asc()).limit(200).all()
    else:
        events = list(reversed(q.order_by(DeskEvent.id.desc()).limit(60).all()))

    trades = (db.query(DeskTrade).filter(DeskTrade.worker == worker)
              .order_by(DeskTrade.id.desc()).limit(40).all())
    closed = (db.query(DeskTrade.profit_usd, DeskTrade.r_multiple)
              .filter(DeskTrade.worker == worker, DeskTrade.is_open.is_(False)).all())
    rs = [r for _, r in closed if r is not None]
    profits = [p for p, _ in closed if p is not None]

    return {
        "worker": worker,
        "online": age is not None and age < STALE_AFTER_SEC,
        "age_sec": round(age, 1) if age is not None else None,
        "state": row.payload if row else None,
        "events": [{
            "id": e.id, "at": e.created_at.isoformat() if e.created_at else None, "symbol": e.symbol,
            "agent": e.agent, "level": e.level, "message": e.message, "data": e.data,
        } for e in events],
        "trades": [_trade_json(t) for t in trades],
        "summary": {
            "closed_trades": len(closed),
            "wins": sum(1 for p in profits if p > 0),
            "win_rate": round(100 * sum(1 for p in profits if p > 0) / len(profits), 1) if profits else None,
            "net_profit": round(sum(profits), 2) if profits else 0.0,
            "total_r": round(sum(rs), 2) if rs else 0.0,
            "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
        },
    }
