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
# USER SIDE (steps 11-12) -- /api/client/desk/*, authenticated by the user's licence key like every
# other /api/client route (the app's proxy only relays /api/client/...).
# The Desk IS the AI Auto Trader (step 12): it trades a NOLIMITZ PRO account whenever that account's
# AI Auto Trader is ON. start/stop flip that same switch (ClientMT5Account.ai_auto_trade).
# Gate while testing: DESK_PUBLIC_ENABLED=true opens it to every Pro licence; otherwise only the licence
# ids or keys in DESK_BETA_LICENSES (comma separated). Rules live in app/desk/access.py.
# =====================================================================================
from app.desk.access import TRADES_PER_MODE, desk_public, is_pro, licence_live, open_to  # noqa: E402


class DeskLicence(BaseModel):
    license_key: str


def _desk_licence(db: Session, license_key: str):
    from app.models import License
    lic = db.query(License).filter(License.license_key == (license_key or "").strip()).first()
    if lic is None:
        raise HTTPException(status_code=400, detail="Invalid license key")
    return lic


def _desk_open_to(lic) -> bool:
    return open_to(lic)


def st_open(db: Session, lic) -> bool:
    acct = _account(db, lic)
    return open_to(lic, acct.login if acct else None)


def _account(db: Session, lic):
    from app.models import ClientMT5Account
    return (db.query(ClientMT5Account).filter(ClientMT5Account.license_id == lic.id,
                                              ClientMT5Account.is_active == True)  # noqa: E712
            .order_by(ClientMT5Account.id.desc()).first())


def _desk_payload(db: Session):
    st = db.query(DeskState).filter(DeskState.worker == "desk-1").first()
    if st is None:
        return {}, None
    age = None
    if st.updated_at:
        ua = st.updated_at if st.updated_at.tzinfo else st.updated_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ua).total_seconds()
    return (st.payload or {}), age


def _desk_status(db: Session, lic) -> dict:
    from app.models import TradeExecution
    from app.routers.copier import find_symbol_setting
    acct = _account(db, lic)
    gold = find_symbol_setting(db, lic.id, "XAUUSD")
    mode = ((acct.risk_level if acct else None) or "medium").lower()
    per_signal = TRADES_PER_MODE.get(mode, 3)
    lot = None
    try:
        lot = float(gold.lot_size) if gold else None
    except (TypeError, ValueError):
        lot = None
    pro = is_pro(lic)
    checks = [
        {"ok": pro, "text": "NOLIMITZ PRO licence"},
        {"ok": licence_live(lic), "text": "Licence active"},
        {"ok": bool(acct and acct.is_verified), "text": "MT5 account connected and verified"},
        {"ok": bool(lot and lot > 0), "text": "Gold (XAUUSD) enabled with a lot size"},
        {"ok": bool(acct and acct.ai_auto_trade), "text": "AI Auto Trader switched ON"},
    ]
    # rough risk preview: gold stop = 1.5 x the Desk's current H1 ATR; 0.01 lot ~ $1 per $1 move
    est = None
    payload, _ = _desk_payload(db)
    try:
        atr = float((payload.get("symbols") or {}).get("XAUUSD", {}).get("atr") or 0)
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
    available = open_to(lic, acct.login if acct else None)
    ready = all(c["ok"] for c in checks[:4])
    return {
        "pro": pro,
        "available": available,
        "public": desk_public(),
        # following = the Desk will put its next trade on this account
        "following": bool(available and ready and acct and acct.ai_auto_trade),
        "ready": ready,                      # everything except the on/off switch itself
        "robot_on": bool(acct and acct.ai_auto_trade),
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
    """Everything the Desk tab needs to show the AI Auto Trader switch for THIS user."""
    return _desk_status(db, _desk_licence(db, license_key))


@router.post("/api/client/desk/start")
def client_desk_start(data: DeskLicence, db: Session = Depends(get_db)):
    """Turn the AI Auto Trader ON (the Desk trades this account from its next signal)."""
    lic = _desk_licence(db, data.license_key)
    if not is_pro(lic):
        raise HTTPException(status_code=403, detail="The Desk is for NOLIMITZ PRO licences")
    if not st_open(db, lic):
        raise HTTPException(status_code=403, detail="The Desk is not open for new accounts yet")
    st = _desk_status(db, lic)
    missing = [c["text"] for c in st["checks"][:4] if not c["ok"]]
    if missing:
        raise HTTPException(status_code=409, detail="Before starting the Desk: " + "; ".join(missing))
    if st["estimate"] and st["estimate"]["blocked"]:
        raise HTTPException(status_code=409, detail=(
            f"Your gold lot x {st['trades_per_signal']} trades would risk about ${st['estimate']['risk_usd']:.2f} "
            "per Desk trade, more than your balance. Lower the gold lot or set risk mode to Normal."))
    acct = _account(db, lic)
    acct.ai_auto_trade = True
    db.commit()
    out = _desk_status(db, lic)
    out["message"] = "AI Auto Trader ON: the Desk's next gold trade opens on your account with your own lot and risk mode."
    return out


@router.post("/api/client/desk/stop")
def client_desk_stop(data: DeskLicence, db: Session = Depends(get_db)):
    """AI Auto Trader OFF: no NEW trades. Trades already open keep being managed until they close."""
    lic = _desk_licence(db, data.license_key)
    acct = _account(db, lic)
    if acct is not None:
        acct.ai_auto_trade = False
        db.commit()
    out = _desk_status(db, lic)
    out["message"] = "AI Auto Trader OFF: no new trades. Open Desk trades are still managed until they close."
    return out


def _seg(text, tag="XAU"):
    """The gold part of a worker card ('XAU: ... | BTC: ...' -> 'XAU: ...')."""
    if not text:
        return None
    for part in str(text).split(" | "):
        if part.strip().upper().startswith(tag):
            return part.strip()
    return None


def _money(x):
    try:
        return f"${float(x):,.2f}"
    except (TypeError, ValueError):
        return "-"


def _source(master_ticket) -> str:
    mt = str(master_ticket or "")
    if mt.startswith("DESK-"):
        return "AI"
    if mt.startswith("TG"):
        return "TELEGRAM"
    return "MASTER"


def _lvl(v):
    """Executor level encodings -> words: 'dist:20.6' -> '20.60 from entry', 'off:0' -> 'entry +0.00'."""
    if v in (None, ""):
        return None
    v = str(v)
    try:
        if v.startswith("dist:"):
            return f"{float(v[5:]):.2f} from entry"
        if v.startswith("off:"):
            o = float(v[4:])
            return "breakeven (entry)" if abs(o) < 1e-9 else f"entry {o:+.2f}"
        return f"{float(v):.2f}"
    except ValueError:
        return v


def _my_activity(db: Session, lic, limit: int = 60) -> list:
    """Everything that happened on this user's MT5 through NolimitzBots: master copier trades, the AI (Desk)
    trades, Telegram trades, stop moves, closes, failures, and why the AI skipped their account."""
    from app.models import TradeExecution
    from app.desk.models import DeskUserLog
    out = []
    for r in (db.query(TradeExecution).filter(TradeExecution.license_id == lic.id)
              .order_by(TradeExecution.id.desc()).limit(limit).all()):
        out.append({"at": r.created_at.isoformat() if r.created_at else None, "source": _source(r.master_ticket),
                    "event": r.event_type, "symbol": r.symbol, "action": r.action, "lot": r.lot_size,
                    "sl": _lvl(r.sl), "tp": _lvl(r.tp), "price": r.price, "status": r.status,
                    "note": r.error_message, "ticket": r.client_ticket})
    for n in (db.query(DeskUserLog).filter(DeskUserLog.license_id == lic.id)
              .order_by(DeskUserLog.id.desc()).limit(20).all()):
        out.append({"at": n.created_at.isoformat() if n.created_at else None, "source": "AI", "event": "skipped",
                    "symbol": n.symbol, "action": n.action, "lot": None, "sl": None, "tp": None, "price": None,
                    "status": "not placed", "note": n.message, "ticket": None})
    out.sort(key=lambda a: a["at"] or "", reverse=True)
    return out[:limit]


@router.get("/api/client/desk/view")
def client_desk_view(license_key: str = Query(..., max_length=128), db: Session = Depends(get_db)):
    """The Desk as THIS user sees it: their own balance, their own Desk trades, and each agent's card
    written for their account. Nothing from the Desk's own demo account (balance, profit, lots) is returned."""
    from app.models import TradeTicketMap
    lic = _desk_licence(db, license_key)
    st = _desk_status(db, lic)
    payload, age = _desk_payload(db)
    online = age is not None and age < STALE_AFTER_SEC
    agents_src = payload.get("agents") or {}
    xau = (payload.get("symbols") or {}).get("XAUUSD") or {}
    mkt_open = bool(xau.get("open"))
    acc = st["account"] or {}
    bal = float(acc.get("balance") or 0)
    eq = float(acc.get("equity") or 0)
    lot, n = st["gold_lot"], st["trades_per_signal"]

    # this user's open Desk copies
    mine = (db.query(TradeTicketMap).filter(TradeTicketMap.license_id == lic.id,
                                            TradeTicketMap.master_ticket.like("DESK-%"),
                                            TradeTicketMap.is_closed == False)  # noqa: E712
            .order_by(TradeTicketMap.id.desc()).all())
    desk_pos = {f"DESK-{p.get('ticket')}": p for p in (payload.get("positions") or []) if not p.get("paper")}
    rows = {f"DESK-{t.ticket}": t for t in db.query(DeskTrade).filter(DeskTrade.is_open.is_(True)).all()}
    open_list, mgmt = [], []
    for m in mine:
        p, row = desk_pos.get(m.master_ticket), rows.get(m.master_ticket)
        side = None
        if p is not None:
            side = "buy" if p.get("direction", 0) > 0 else "sell"
        elif row is not None:
            side = "buy" if row.direction > 0 else "sell"
        state = None
        if row is not None and p is not None and p.get("sl") is not None:
            locked = (p["sl"] - row.entry_price) * row.direction >= -1e-9
            if locked and abs(p["sl"] - row.entry_price) < 0.01:
                state = "stop at breakeven"
            elif locked:
                state = "trailing stop, profit locked"
            else:
                state = "original stop · breakeven at +1R"
        open_list.append({"symbol": m.symbol, "client_ticket": m.client_ticket, "master_ticket": m.master_ticket,
                          "side": side, "management": state,
                          "opened_at": m.created_at.isoformat() if m.created_at else None})
        if state:
            mgmt.append(state)

    NEED = {"NOLIMITZ PRO licence": "needs a NOLIMITZ PRO licence", "Licence active": "your licence has expired",
            "MT5 account connected and verified": "connect and verify your MT5 account",
            "Gold (XAUUSD) enabled with a lot size": "set a gold (XAUUSD) lot size",
            "AI Auto Trader switched ON": "turn the AI Auto Trader ON"}
    sources = {str(m.client_ticket): {"source": _source(m.master_ticket), "master_ticket": m.master_ticket}
               for m in db.query(TradeTicketMap).filter(TradeTicketMap.license_id == lic.id,
                                                        TradeTicketMap.is_closed == False)  # noqa: E712
               if m.client_ticket}
    off_reason = next((NEED.get(c["text"], c["text"]) for c in st["checks"] if not c["ok"]), None)
    if not st["available"] and st["pro"]:
        off_reason = "Desk not open to your account yet"

    def scout():
        if not online:
            return "Desk offline · reconnecting"
        if not mkt_open:
            return "gold market closed · resumes at the open"
        return (xau.get("scout") or _seg(agents_src.get("scout")) or "reading gold").replace("XAU: ", "")

    def check():
        parts = []
        c = _seg(agents_src.get("check"))
        parts.append((c or ("gold market closed" if not mkt_open else "checking gold")).replace("XAU ", "gold "))
        parts.append("your MT5 " + ("connected" if acc.get("verified") else "NOT connected"))
        return " · ".join(parts)

    def size():
        if not lot:
            return "set a gold (XAUUSD) lot size to trade"
        txt = f"your next gold: {lot:.2f} lot × {n} trade{'s' if n != 1 else ''}"
        e = st["estimate"]
        if e:
            txt += f" · risk ≈ {_money(e['risk_usd'])}"
            if e.get("risk_pct") is not None:
                txt += f" ({e['risk_pct']}% of {_money(bal)})"
            if e.get("high_risk"):
                txt += " · HIGH"
        return txt

    def entry():
        if not st["following"]:
            return "not trading your account · " + (off_reason or "AI Auto Trader is OFF")
        if open_list:
            return f"in a gold trade · {len(open_list)} on your account"
        if not mkt_open:
            return "gold market closed"
        return "armed · waits for Scout's next gold setup"

    def exit_():
        if not open_list:
            return "flat · no Desk trades on your account"
        sides = {o["side"] for o in open_list if o["side"]}
        txt = f"managing {len(open_list)} Desk trade{'s' if len(open_list) != 1 else ''}"
        if sides:
            txt += " (" + "/".join(sorted(sides)) + ")"
        if mgmt:
            txt += " · " + mgmt[0]
        return txt

    def guard():
        if not bal:
            return "waiting for your account balance"
        floating = eq - bal
        return (f"your balance {_money(bal)} · equity {_money(eq)} ({floating:+,.2f}) · "
                "blocks any trade that could cost more than your balance")

    return {
        "online": online, "market_open": mkt_open, "updated": payload.get("updated"),
        "following": st["following"], "robot_on": st["robot_on"], "available": st["available"],
        "pro": st["pro"], "ready": st["ready"], "checks": st["checks"], "off_reason": None if st["following"] else off_reason,
        "account": {"balance": acc.get("balance"), "equity": acc.get("equity"),
                    "floating": round(eq - bal, 2) if bal else None, "login": acc.get("login"),
                    "server": acc.get("server"), "broker": acc.get("broker")},
        "risk_mode": st["risk_mode"], "trades_per_signal": n, "gold_lot": lot, "estimate": st["estimate"],
        "agents": {"scout": scout(), "check": check(), "size": size(), "entry": entry(), "exit": exit_(),
                   "guard": guard()},
        "gold": {"bias": xau.get("bias"), "bid": xau.get("bid"), "ask": xau.get("ask")},
        "open_trades": open_list,
        "position_sources": sources,         # MT5 ticket -> AI | MASTER | TELEGRAM, for labelling live positions
        "activity": _my_activity(db, lic),
    }
