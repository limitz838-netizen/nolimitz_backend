"""
app/routers/desk.py -- read-only API for the Desk page (Oct 2026)

  GET /api/desk/state?worker=desk-1&after_id=<last event id seen>
  GET /api/desk/live      the Desk page itself (bot office, chart, log)

Returns everything the Desk page draws in one call, so the page can poll it
every few seconds: the worker's latest snapshot (balance, equity, positions,
candles, agent status lines), new events since after_id, recent trades and a
running summary.

The two GET routes are read-only and public on purpose: they show the Desk demo
account's results, never credentials. The worker masks the MT5 login before
writing the snapshot.

  POST /api/desk/copy/event     the Desk worker reports a real trade (step 9)
  POST /api/desk/copy/follow    switch the Desk on/off for one MT5 login
  GET  /api/desk/copy/followers who follows the Desk

The /copy routes need the machine X-Worker-Token. They never place an order
themselves: they queue rows for the existing copier lanes (see app/desk/copy.py).
"""
from datetime import datetime, timezone
from pathlib import Path

import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.desk.models import DeskEvent, DeskFollower, DeskState, DeskTrade

router = APIRouter(tags=["desk"])   # full paths below: /api/desk/* and /api/client/desk/*

STALE_AFTER_SEC = 60
PAGE = Path(__file__).resolve().parent.parent / "desk" / "static" / "desk.html"


@router.get("/api/desk/live", response_class=HTMLResponse, include_in_schema=False)
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


@router.get("/api/desk/state")
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


# =====================================================================================
# COPY TO USERS (step 9) -- machine routes, protected by the same X-Worker-Token as /worker/*
# =====================================================================================
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")


def _require_worker(x_worker_token: Optional[str]):
    if not WORKER_TOKEN or x_worker_token != WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="worker token required")


class DeskCopyEvent(BaseModel):
    event: str                          # open | modify | close
    ticket: str                         # the Desk's own MT5 ticket
    symbol: str                         # canonical, e.g. XAUUSD
    action: Optional[str] = None        # buy | sell (open only)
    sl_distance: Optional[float] = None
    tp_distance: Optional[float] = None
    stop_offset: Optional[float] = None  # new stop minus the Desk's entry (modify only)
    price: Optional[float] = None


@router.post("/api/desk/copy/event", include_in_schema=False)
def desk_copy_event(payload: DeskCopyEvent, x_worker_token: str = Header(None), db: Session = Depends(get_db)):
    """The Desk worker reports a real trade event; queue it for every follower's account."""
    _require_worker(x_worker_token)
    from app.desk.copy import fan_out
    return fan_out(db, payload.event, payload.ticket, payload.symbol, payload.action, payload.sl_distance,
                   payload.tp_distance, payload.stop_offset, payload.price)


class DeskFollowRequest(BaseModel):
    mt5_login: str
    enabled: bool = True


@router.post("/api/desk/copy/follow", include_in_schema=False)
def desk_copy_follow(payload: DeskFollowRequest, x_worker_token: str = Header(None), db: Session = Depends(get_db)):
    """Switch the Desk on or off for one MT5 account (by login). Admin/test tool until the
    in-app switch exists. Turning it off stops new entries only; open copies are still managed."""
    _require_worker(x_worker_token)
    from app.models import ClientMT5Account
    acct = (db.query(ClientMT5Account).filter(ClientMT5Account.login == str(payload.mt5_login).strip(),
                                              ClientMT5Account.is_active == True)  # noqa: E712
            .order_by(ClientMT5Account.id.desc()).first())
    if acct is None or not acct.license_id:
        raise HTTPException(status_code=404, detail="no active MT5 account with that login")
    row = db.query(DeskFollower).filter(DeskFollower.license_id == acct.license_id).first()
    if row is None:
        row = DeskFollower(license_id=acct.license_id, mt5_login=str(acct.login), enabled=payload.enabled)
        db.add(row)
    else:
        row.enabled = payload.enabled
        row.mt5_login = str(acct.login)
    db.commit()
    return {"license_id": acct.license_id, "mt5_login": acct.login, "enabled": row.enabled,
            "verified": bool(acct.is_verified), "risk_level": acct.risk_level}


@router.get("/api/desk/copy/followers", include_in_schema=False)
def desk_copy_followers(x_worker_token: str = Header(None), db: Session = Depends(get_db)):
    _require_worker(x_worker_token)
    return [{"license_id": r.license_id, "mt5_login": r.mt5_login, "enabled": r.enabled,
             "since": r.created_at.isoformat() if r.created_at else None}
            for r in db.query(DeskFollower).order_by(DeskFollower.id).all()]


# =====================================================================================
# USER SWITCH (step 11) -- /api/client/desk/*, authenticated by the user's licence key like every
# other /api/client route (the app's proxy only relays /api/client/...). A user switches the Desk on
# for THEIR OWN account; their own gold lot and risk mode decide the size.
# Gate while testing: DESK_PUBLIC_ENABLED=true opens it to everyone; otherwise only the licence ids or
# keys listed in DESK_BETA_LICENSES (comma separated) can switch it on.
# =====================================================================================
DESK_PUBLIC = os.getenv("DESK_PUBLIC_ENABLED", "false").strip().lower() == "true"
DESK_BETA = {x.strip() for x in os.getenv("DESK_BETA_LICENSES", "").split(",") if x.strip()}
TRADES_PER_MODE = {"normal": 1, "medium": 3, "aggressive": 5}


class DeskLicence(BaseModel):
    license_key: str


def _desk_licence(db: Session, license_key: str):
    from app.models import License
    lic = db.query(License).filter(License.license_key == (license_key or "").strip()).first()
    if lic is None:
        raise HTTPException(status_code=400, detail="Invalid license key")
    return lic


def _desk_open_to(lic) -> bool:
    return DESK_PUBLIC or str(lic.id) in DESK_BETA or lic.license_key in DESK_BETA


def _desk_status(db: Session, lic) -> dict:
    from app.models import ClientMT5Account, ClientSymbolSetting, TradeExecution
    from app.routers.copier import find_symbol_setting
    now = datetime.now(timezone.utc)
    exp = lic.expires_at if (lic.expires_at is None or lic.expires_at.tzinfo) else lic.expires_at.replace(tzinfo=timezone.utc)
    acct = (db.query(ClientMT5Account).filter(ClientMT5Account.license_id == lic.id, ClientMT5Account.is_active == True)  # noqa: E712
            .order_by(ClientMT5Account.id.desc()).first())
    gold = find_symbol_setting(db, lic.id, "XAUUSD")
    fol = db.query(DeskFollower).filter(DeskFollower.license_id == lic.id).first()
    mode = ((acct.risk_level if acct else None) or "medium").lower()
    per_signal = TRADES_PER_MODE.get(mode, 3)
    lot = None
    try:
        lot = float(gold.lot_size) if gold else None
    except (TypeError, ValueError):
        lot = None
    checks = [
        {"ok": bool(lic.is_active and (exp is None or exp > now)), "text": "Licence active"},
        {"ok": bool(acct and acct.is_verified), "text": "MT5 account connected and verified"},
        {"ok": bool(acct and acct.ai_auto_trade), "text": "Robot switched ON"},
        {"ok": bool(lot and lot > 0), "text": "Gold (XAUUSD) enabled with a lot size"},
    ]
    # rough risk preview: gold stop = 1.5 x the Desk's current H1 ATR; 0.01 lot ~ $1 per $1 move
    est = None
    st = db.query(DeskState).filter(DeskState.worker == "desk-1").first()
    try:
        atr = float(((st.payload or {}).get("symbols") or {}).get("XAUUSD", {}).get("atr") or 0) if st else 0
    except (TypeError, ValueError, AttributeError):
        atr = 0
    if atr and lot:
        stop = 1.5 * atr
        risk = stop * lot * 100 * per_signal
        bal = float(acct.balance or 0) if acct else 0
        est = {"stop_points": round(stop, 2), "risk_usd": round(risk, 2),
               "risk_pct": round(100 * risk / bal, 1) if bal > 0 else None,
               "high_risk": bool(bal > 0 and risk / bal > 0.10), "blocked": bool(bal > 0 and risk > bal)}
    rows = (db.query(TradeExecution).filter(TradeExecution.license_id == lic.id,
                                            TradeExecution.master_ticket.like("DESK-%"))
            .order_by(TradeExecution.id.desc()).limit(30).all())
    return {
        "available": _desk_open_to(lic),
        "following": bool(fol and fol.enabled),
        "ready": all(c["ok"] for c in checks),
        "checks": checks,
        "account": None if acct is None else {
            "login": (str(acct.login)[:2] + "***" + str(acct.login)[-3:]) if acct.login else None,
            "server": acct.server, "broker": getattr(acct, "broker_name", None),
            "balance": acct.balance, "equity": acct.equity, "verified": bool(acct.is_verified),
            "robot_on": bool(acct.ai_auto_trade)},
        "risk_mode": mode, "trades_per_signal": per_signal, "gold_lot": lot,
        "estimate": est,
        "activity": [{"at": r.created_at.isoformat() if r.created_at else None, "event": r.event_type,
                      "symbol": r.symbol, "action": r.action, "lot": r.lot_size, "status": r.status,
                      "note": r.error_message, "ticket": r.client_ticket} for r in rows],
    }


@router.get("/api/client/desk/status")
def client_desk_status(license_key: str = Query(..., max_length=128), db: Session = Depends(get_db)):
    """Everything the Desk tab needs to show the Start switch for THIS user."""
    return _desk_status(db, _desk_licence(db, license_key))


@router.post("/api/client/desk/start")
def client_desk_start(data: DeskLicence, db: Session = Depends(get_db)):
    lic = _desk_licence(db, data.license_key)
    if not _desk_open_to(lic):
        raise HTTPException(status_code=403, detail="The Desk is not open for new accounts yet")
    st = _desk_status(db, lic)
    missing = [c["text"] for c in st["checks"] if not c["ok"]]
    if missing:
        raise HTTPException(status_code=409, detail="Before starting the Desk: " + "; ".join(missing))
    if st["estimate"] and st["estimate"]["blocked"]:
        raise HTTPException(status_code=409, detail=(
            f"Your gold lot x {st['trades_per_signal']} trades would risk about ${st['estimate']['risk_usd']:.2f} "
            "per Desk trade, more than your balance. Lower the gold lot or set risk mode to Normal."))
    from app.models import ClientMT5Account
    acct = (db.query(ClientMT5Account).filter(ClientMT5Account.license_id == lic.id, ClientMT5Account.is_active == True)  # noqa: E712
            .order_by(ClientMT5Account.id.desc()).first())
    row = db.query(DeskFollower).filter(DeskFollower.license_id == lic.id).first()
    if row is None:
        row = DeskFollower(license_id=lic.id, mt5_login=str(acct.login) if acct else None, enabled=True)
        db.add(row)
    else:
        row.enabled = True
        row.mt5_login = str(acct.login) if acct else row.mt5_login
    db.commit()
    out = _desk_status(db, lic)
    out["message"] = "Desk started: its next trade will open on your account with your own lot and risk mode."
    return out


@router.post("/api/client/desk/stop")
def client_desk_stop(data: DeskLicence, db: Session = Depends(get_db)):
    """Stops NEW Desk trades on this account. Trades already open keep being managed until they close."""
    lic = _desk_licence(db, data.license_key)
    row = db.query(DeskFollower).filter(DeskFollower.license_id == lic.id).first()
    if row is not None:
        row.enabled = False
        db.commit()
    out = _desk_status(db, lic)
    out["message"] = "Desk stopped: no new Desk trades. Open Desk trades are still managed until they close."
    return out
