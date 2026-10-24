"""
app/desk/copy.py -- the Desk as a signal source for users' own MT5 accounts (Oct 2026, step 9)

  Desk worker (VPS)  --POST /api/desk/copy/event-->  this file  -->  CopierTradeEvent + TradeExecution
                                                                       |
                                                existing copier fast lane + copier_executor (VPS)
                                                                       |
                                                              each follower's own MT5

The Desk decides WHEN to trade and WHERE the stop and target go. Each user decides HOW MUCH:
  lot          = the user's own setting for the symbol (ClientSymbolSetting.lot_size)
  trades/signal= the user's risk mode (normal 1, medium 3, aggressive 5) -- per_signal stays NULL
  stop/target  = DISTANCES, not prices ("dist:20.616"), so every broker measures them from its own
                 fill price. Brokers quote gold slightly differently; a copied price level could sit
                 on the wrong side on another broker.
  breakeven / trailing = an OFFSET from the user's own entry ("off:0" = breakeven), only ever tightening.

Who: every NOLIMITZ PRO account with the AI Auto Trader ON receives NEW Desk trades (app/desk/access.py). Close and modify go
to every licence that still holds a copy, so switching the Desk off never strands an open trade.
The copier executor's Desk Guard (VPS) reads each user's real equity before every entry: it blocks a
trade whose stop would cost more than the whole account and labels anything above 10% as high risk.

Nothing here places an order. It writes the same rows the master copier and Telegram copier write.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.desk.access import licence_live, open_to
from app.models import (ClientMT5Account, CopierTradeEvent, ExpertAdvisor, License, TradeExecution,
                        TradeTicketMap)

log = logging.getLogger("desk.copy")
TICKET_PREFIX = "DESK-"
LABEL = "Nolimitz AI"   # comment on the user's MT5 position: Desk trades are the AI's trades


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _symbol_setting(db: Session, license_id: int, symbol: str):
    from app.routers.copier import find_symbol_setting   # same canonical matcher the copier uses
    return find_symbol_setting(db, license_id, symbol)


def _row(db: Session, lic: License, event_type: str, master_ticket: str, symbol: str, action: Optional[str],
         lot: Optional[str], sl: Optional[str], tp: Optional[str], price: Optional[str]) -> TradeExecution:
    ea = db.query(ExpertAdvisor).filter(ExpertAdvisor.id == lic.ea_id).first()
    ev = CopierTradeEvent(source_admin_id=lic.admin_id, ea_id=lic.ea_id,
                          ea_code=(getattr(ea, "ea_code", None) or "DESK"), event_type=event_type,
                          master_ticket=master_ticket, symbol=symbol, action=action, lot_size=None,
                          sl=sl, tp=tp, price=price, comment=LABEL, status="pending")
    db.add(ev)
    db.flush()
    ex = TradeExecution(copier_event_id=ev.id, license_id=lic.id, ea_id=lic.ea_id, master_ticket=master_ticket,
                        client_ticket=None, symbol=symbol, action=action, lot_size=lot, sl=sl, tp=tp,
                        price=price, comment=LABEL, event_type=event_type, status="pending", per_signal=None)
    db.add(ex)
    db.flush()
    return ex


def _holders(db: Session, master_ticket: str) -> List[int]:
    """Licences that hold (or are about to hold) a copy of this Desk ticket."""
    held = {lid for (lid,) in db.query(TradeTicketMap.license_id).filter(
        TradeTicketMap.master_ticket == master_ticket, TradeTicketMap.is_closed == False).all()}  # noqa: E712
    flying = {lid for (lid,) in db.query(TradeExecution.license_id).filter(
        TradeExecution.master_ticket == master_ticket, TradeExecution.event_type == "open",
        TradeExecution.status.in_(("pending", "processing"))).all()}
    return sorted(held | flying)


def fan_out(db: Session, event: str, desk_ticket: str, symbol: str, action: Optional[str] = None,
            sl_distance: Optional[float] = None, tp_distance: Optional[float] = None,
            stop_offset: Optional[float] = None, price: Optional[float] = None) -> dict:
    """Queue one Desk event for every eligible account. Returns a small report for the worker's log."""
    event = (event or "").lower()
    action = (action or "").lower() or None          # MUST be lowercase: the executor tests == "buy"
    symbol = (symbol or "").upper()
    master_ticket = f"{TICKET_PREFIX}{desk_ticket}"
    now = datetime.now(timezone.utc)
    queued, skipped = [], []

    if event == "open":
        if action not in ("buy", "sell") or not sl_distance or sl_distance <= 0:
            return {"queued": 0, "error": "open needs action buy/sell and a positive sl_distance"}
        # The Desk IS the AI Auto Trader: every Pro account with the AI Auto Trader ON receives it
        # (see app/desk/access.py). One account per licence, newest row wins.
        accts = (db.query(ClientMT5Account).filter(ClientMT5Account.is_active == True,       # noqa: E712
                                                   ClientMT5Account.is_verified == True,     # noqa: E712
                                                   ClientMT5Account.ai_auto_trade == True)   # noqa: E712
                 .order_by(ClientMT5Account.id.desc()).all())
        seen = set()
        for acct in accts:
            if not acct.license_id or acct.license_id in seen:
                continue
            seen.add(acct.license_id)
            lic = db.query(License).filter(License.id == acct.license_id).first()
            if lic is None or not licence_live(lic, now):
                skipped.append((acct.license_id, "licence inactive or expired"))
                continue
            if not open_to(lic, acct.login):
                skipped.append((lic.id, "not a NOLIMITZ PRO licence or Desk not open to it yet"))
                continue
            setting = _symbol_setting(db, lic.id, symbol)
            if setting is None or not setting.lot_size:
                skipped.append((lic.id, f"{symbol} not enabled or no lot set"))
                continue
            want = (getattr(setting, "trade_direction", None) or "both").lower()
            if want in ("buy", "sell") and want != action:
                skipped.append((lic.id, f"user accepts {want.upper()} only"))
                continue
            dup = db.query(TradeExecution.id).filter(TradeExecution.license_id == lic.id,
                                                     TradeExecution.master_ticket == master_ticket,
                                                     TradeExecution.event_type == "open").first()
            if dup:
                skipped.append((lic.id, "already queued"))
                continue
            ex = _row(db, lic, "open", master_ticket, symbol, action, str(setting.lot_size),
                      f"dist:{float(sl_distance):.6f}",
                      f"dist:{float(tp_distance):.6f}" if tp_distance and tp_distance > 0 else None,
                      str(price) if price else None)
            queued.append(ex.id)
    elif event in ("modify", "close"):
        if event == "modify" and stop_offset is None:
            return {"queued": 0, "error": "modify needs stop_offset"}
        for lid in _holders(db, master_ticket):
            lic = db.query(License).filter(License.id == lid).first()
            if lic is None:
                continue
            ex = _row(db, lic, event, master_ticket, symbol, None, None,
                      f"off:{float(stop_offset):.6f}" if event == "modify" else None, None, None)
            queued.append(ex.id)
    else:
        return {"queued": 0, "error": f"unknown event {event}"}

    if event == "open" and skipped:
        from app.desk.models import DeskUserLog
        for lid, why in skipped:
            if lid and why != "already queued":
                db.add(DeskUserLog(license_id=lid, symbol=symbol, action=action,
                                   message=f"AI {action.upper() if action else ''} {symbol} not placed on your account: {why}"[:240]))
    db.commit()
    if event == "open" and queued:
        try:
            from app.desk.notify import schedule
            schedule(master_ticket, symbol, action)
        except Exception as e:                       # a notification must never break copying
            log.warning("desk notify schedule failed: %s", e)
    log.info("desk copy %s %s: queued %d, skipped %d", event, master_ticket, len(queued), len(skipped))
    return {"event": event, "master_ticket": master_ticket, "queued": len(queued),
            "skipped": [{"license_id": a, "reason": b} for a, b in skipped]}
