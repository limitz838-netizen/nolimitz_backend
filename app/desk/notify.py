"""
app/desk/notify.py -- tell users when the AI Desk acts on THEIR account (Oct 2026, step 19)

After the Desk opens a trade, fan_out() queues one row per eligible account. The copier on the VPS then opens
it (status 'executed') or refuses it with a reason ('skipped' / 'failed', e.g. "risk mode medium allows 3 open
on XAUUSD, 3 already open"). About two minutes later this module reads those outcomes and emails each user once:
  opened  -> "AI Desk opened SELL XAUUSD on your account"   (lot, stop, target)
  refused -> "AI Desk could not open a trade on your account" with the reason and how to fix it
Nothing here places or changes a trade. Email goes through the same Resend helper as licence emails.
Off with DESK_NOTIFY_ENABLED=false.
"""
from __future__ import annotations

import html
import logging
import os
import threading

log = logging.getLogger("desk.notify")
DELAY_SEC = float(os.getenv("DESK_NOTIFY_DELAY_SEC", "120"))


def enabled() -> bool:
    return os.getenv("DESK_NOTIFY_ENABLED", "true").strip().lower() == "true"


FIX = {
    "risk mode": "Your risk mode's limit of open trades on this symbol was already full. Close a trade or raise "
                 "your risk mode in Settings if you want the AI's trades to fit alongside your other trades.",
    "robot stopped": "The AI Auto Trader is switched off on your account. Switch it on in your dashboard.",
    "margin": "Your account did not have enough free margin. Lower your lot size or add funds.",
}


def _fix_for(reason: str) -> str:
    r = (reason or "").lower()
    for k, v in FIX.items():
        if k in r:
            return v
    return "Check your settings in the dashboard, or contact support if this keeps happening."


def _html(title: str, lines: list) -> str:
    body = "".join(f"<p style='margin:0 0 10px'>{html.escape(x)}</p>" for x in lines)
    return (f"<div style='font-family:Arial,sans-serif;font-size:15px;color:#141413;max-width:520px'>"
            f"<h2 style='font-size:18px;margin:0 0 14px'>{html.escape(title)}</h2>{body}"
            f"<p style='margin:18px 0 0;font-size:12px;color:#6b6a65'>You get this because the AI Auto Trader is on for "
            f"your NolimitzBots account. Trading carries a high risk of loss.</p></div>")


def _run(master_ticket: str, symbol: str, action: str, attempt: int = 1, only=None):
    from app.database import SessionLocal
    from app.models import License, TradeExecution
    from app.routers.license import send_email
    db = SessionLocal()
    try:
        rows = (db.query(TradeExecution).filter(TradeExecution.master_ticket == master_ticket,
                                                TradeExecution.event_type == "open").all())
        by_lic = {}
        for r in rows:
            by_lic.setdefault(r.license_id, []).append(r)
        side = (action or "").upper()
        retry = set()
        for lid, rs in by_lic.items():
            if only is not None and lid not in only:
                continue
            lic = db.query(License).filter(License.id == lid).first()
            if lic is None or not lic.client_email:
                continue
            done = [r for r in rs if (r.status or "").lower() in ("executed", "done", "success", "filled")]
            bad = [r for r in rs if (r.status or "").lower() in ("skipped", "failed", "error", "rejected")]
            if done:
                lots = sum(float(r.lot_size or 0) for r in done)
                subj = f"AI Desk opened {side} {symbol} on your account"
                lines = [f"The AI Desk just opened {len(done)} {side} trade{'s' if len(done) > 1 else ''} on {symbol} "
                         f"on your MT5 account ({lots:.2f} lots in total, using your own lot size and risk mode).",
                         "A stop loss and take profit were set. The AI moves the stop to breakeven at +1R and then "
                         "trails it. Watch it live in the Desk tab of your dashboard."]
            elif bad:
                reason = bad[0].error_message or bad[0].status
                subj = f"AI Desk could not open a {symbol} trade on your account"
                lines = [f"The AI Desk took a {side} trade on {symbol}, but it was not opened on your account.",
                         f"Reason: {reason}", _fix_for(reason)]
            else:                                 # still pending: look once more later, never guess
                if attempt == 1:
                    retry.add(lid)
                continue
            ok, detail = send_email(lic.client_email, subj, _html(subj, lines))
            log.info("desk notify %s licence %s: %s %s", master_ticket, lid, ok, detail)
        if retry:
            t = threading.Timer(300, _run, args=(master_ticket, symbol, action, 2, retry))
            t.daemon = True
            t.start()
    except Exception as e:
        log.warning("desk notify %s failed: %s", master_ticket, e)
    finally:
        db.close()


def schedule(master_ticket: str, symbol: str, action: str, delay: float = None):
    if not enabled():
        return
    t = threading.Timer(DELAY_SEC if delay is None else delay, _run, args=(master_ticket, symbol, action))
    t.daemon = True
    t.start()
