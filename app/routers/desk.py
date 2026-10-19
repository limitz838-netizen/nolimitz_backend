"""
app/routers/desk.py -- read-only API for the Desk page (Oct 2026)

  GET /api/desk/state?worker=desk-1&after_id=<last event id seen>
  GET /api/desk/live      the Desk page itself (bot office, chart, log)

Returns everything the Desk page draws in one call, so the page can poll it
every few seconds: the worker's latest snapshot (balance, equity, positions,
candles, agent status lines), new events since after_id, recent trades and a
running summary.

Read-only and public on purpose: it shows the Desk demo account's results,
never credentials. The worker masks the MT5 login before writing the snapshot.
Nothing in this router can place, change or close a trade.
"""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.desk.models import DeskEvent, DeskState, DeskTrade

router = APIRouter(prefix="/api/desk", tags=["desk"])

STALE_AFTER_SEC = 60
PAGE = Path(__file__).resolve().parent.parent / "desk" / "static" / "desk.html"


@router.get("/live", response_class=HTMLResponse, include_in_schema=False)
def desk_page():
    """The live Desk page. It polls /api/desk/state on the same domain."""
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})


def _trade_json(t: DeskTrade) -> dict:
    return {
        "id": t.id, "ticket": t.ticket, "symbol": t.symbol, "direction": t.direction,
        "lot": t.lot, "entry": t.entry_price, "stop": t.current_stop, "initial_stop": t.initial_stop,
        "target": t.target, "opened_at": t.opened_at.isoformat() if t.opened_at else None,
        "is_open": t.is_open, "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "exit": t.exit_price, "profit": t.profit_usd, "r": t.r_multiple, "reason": t.exit_reason,
        "is_demo": t.is_demo, "paper": (t.ticket or "").startswith("paper-"),
    }


def _summary(rows) -> dict:
    """rows: (profit_usd, r_multiple) of closed trades."""
    rs = [r for _, r in rows if r is not None]
    profits = [p for p, _ in rows if p is not None]
    wins = sum(1 for r in rs if r > 0)
    return {
        "closed_trades": len(rows),
        "wins": wins,
        "win_rate": round(100 * wins / len(rs), 1) if rs else None,
        "net_profit": round(sum(profits), 2) if profits else 0.0,
        "total_r": round(sum(rs), 2) if rs else 0.0,
        "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
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
    closed = (db.query(DeskTrade.profit_usd, DeskTrade.r_multiple, DeskTrade.ticket, DeskTrade.symbol,
                       DeskTrade.opened_at)
              .filter(DeskTrade.worker == worker, DeskTrade.is_open.is_(False)).all())
    real = [(p, r) for p, r, tk, _, _ in closed if not (tk or "").startswith("paper-")]
    paper = {}
    for p, r, tk, sym, opened in closed:
        if (tk or "").startswith("paper-"):
            paper.setdefault(sym, {"rows": [], "since": opened})
            paper[sym]["rows"].append((p, r))
            if opened and (paper[sym]["since"] is None or opened < paper[sym]["since"]):
                paper[sym]["since"] = opened

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
        # Real Desk-account trades only. Paper trades (shadow symbols) never touch the balance.
        "summary": _summary(real),
        # Forward-only paper record per shadow symbol, e.g. BTCUSD on the squeeze rules.
        "paper_summary": {sym: dict(_summary(v["rows"]), since=v["since"].isoformat() if v["since"] else None)
                          for sym, v in paper.items()},
    }
