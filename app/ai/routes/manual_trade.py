"""
================================================================================
  MANUAL TRADE (SELF-EXECUTION FROM CHART SCANNER)  — RENDER / LINUX
================================================================================
  When a user scans a chart and taps "Send to MT5", the frontend calls
  POST /api/client/send-trade. This endpoint does NOT touch MT5 (Render can't).
  It validates the request and writes a user-scoped ManualTradeRequest row.
  The Windows execution worker picks it up on its next cycle and executes it on
  THAT user's account, through the same safe pipeline used for auto-trades
  (live price, the user's saved lot, bounded SL/TP, caps, emergency SL).

  SAFETY GATES enforced here:
    • The user must have a VERIFIED connected MT5 account.
    • The symbol must be one the user has ENABLED in their settings — this
      guarantees a known lot size and that it's a pair they actually connected
      (kills the "wrong symbol from a misread screenshot" risk).
    • BUY / SELL only — NEUTRAL can't be traded.
    • De-dupe: a double-tap won't queue two trades.

  This is self-execution only: the request is scoped to the requesting user's
  own license/account. It never affects any other user.
================================================================================
"""

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models import (
    License, ClientMT5Account, ClientSymbolSetting, ManualTradeRequest,
)

logger = logging.getLogger("manual_trade")

router = APIRouter(prefix="/api/client", tags=["Manual Trade"])

# A second identical PENDING request inside this window is treated as a
# double-tap and ignored (returns the existing one) instead of queuing twice.
DUPLICATE_WINDOW_SEC = 15


class SendTradeRequest(BaseModel):
    license_key: str
    symbol: str
    action: str   # "BUY" | "SELL"


@router.post("/send-trade")
def send_trade(data: SendTradeRequest, db: Session = Depends(get_db)):
    action = (data.action or "").upper().strip()
    if action not in ("BUY", "SELL"):
        raise HTTPException(status_code=400,
                            detail="Action must be BUY or SELL — a NEUTRAL reading can't be traded.")

    symbol = (data.symbol or "").upper().strip().replace(" ", "")
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required.")

    # ── License ──────────────────────────────────────────────────────────────
    lic = db.query(License).filter(License.license_key == data.license_key).first()
    if not lic:
        raise HTTPException(status_code=400, detail="Invalid license key.")

    # ── Must have a VERIFIED connected MT5 account ────────────────────────────
    acct = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == lic.id
    ).first()
    if not acct or not acct.is_verified:
        raise HTTPException(
            status_code=400,
            detail="No verified MT5 account connected. Connect your MT5 account first.",
        )

    # ── Symbol must be one the user ENABLED (gives a known lot + connected pair)
    setting = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == lic.id,
        ClientSymbolSetting.symbol_name == symbol,
    ).first()
    if not setting or not setting.enabled:
        raise HTTPException(
            status_code=400,
            detail=f"{symbol} isn't in your enabled symbols. Add it (with a lot size) "
                   f"in Settings before sending a trade.",
        )

    # ── De-dupe double taps ───────────────────────────────────────────────────
    recent_cutoff = datetime.now(timezone.utc) - timedelta(seconds=DUPLICATE_WINDOW_SEC)
    dup = db.query(ManualTradeRequest).filter(
        ManualTradeRequest.license_id == lic.id,
        ManualTradeRequest.symbol == symbol,
        ManualTradeRequest.action == action,
        ManualTradeRequest.status == "PENDING",
    ).order_by(ManualTradeRequest.id.desc()).first()
    if dup:
        return {
            "success": True, "queued": True, "request_id": dup.id,
            "message": "Already queued — it will execute on your account shortly.",
        }

    # ── Queue it ──────────────────────────────────────────────────────────────
    req = ManualTradeRequest(
        license_id = lic.id,
        mt5_login  = acct.login,
        symbol     = symbol,
        action     = action,
        source     = "chart_scan",
        status     = "PENDING",
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    logger.info("📨 Manual trade queued: %s %s for login=%s (request #%d)",
                action, symbol, acct.login, req.id)

    return {
        "success": True,
        "queued": True,
        "request_id": req.id,
        "message": f"{action} {symbol} sent to your MT5 — it will execute on your "
                   f"account within a few seconds.",
    }


@router.get("/send-trade-status")
def send_trade_status(request_id: int, db: Session = Depends(get_db)):
    """
    Frontend polls this after queuing so it can show executed / failed instead
    of leaving the user guessing.
    """
    req = db.query(ManualTradeRequest).filter(
        ManualTradeRequest.id == request_id
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    return {
        "request_id":   req.id,
        "status":       req.status,        # PENDING | DONE | FAILED
        "symbol":       req.symbol,
        "action":       req.action,
        "mt5_ticket":   req.mt5_ticket,
        "error":        req.error,
        "processed_at": req.processed_at.isoformat() if req.processed_at else None,
    }