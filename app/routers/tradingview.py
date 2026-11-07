"""TradingView -> MT5, as a source of the Telegram Copier (step 41, Nov 2026).

A licence connects TradingView once and gets a private webhook URL. Every alert TradingView
posts there is handled EXACTLY like a message from a followed Telegram channel: same parser,
same per-source settings (Shadow/Live, buy/sell switches, symbols, max lot, trading hours,
closures), same risk gate, same signal log, same copier lanes. Nothing new trades on its own.

How it fits without a new table: the source is an ordinary telegram_sources row whose
chat_id is "tv:<secret>". The secret IS the webhook password, so only that licence's URL can
reach that source. The Telegram listener never sees these rows (listener_sessions skips "tv:").

Alert message: either plain text the Telegram parser already reads
    BUY XAUUSD SL 4050 TP 4100            CLOSE XAUUSD
or JSON
    {"action": "buy", "symbol": "XAUUSD", "sl": 4050, "tp": 4100}
    {"action": "{{strategy.order.action}}", "symbol": "{{ticker}}", "price": "{{close}}"}
"""
import hashlib
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models_telegram import TelegramSource
from app.routers.telegram_user_copier import (
    IncomingSignal, _licence, _require_telegram, incoming_signal, plan_for,
)

logger = logging.getLogger(__name__)

PREFIX = "tv:"
TITLE = "TradingView"
BASE_URL = os.getenv("BACKEND_BASE_URL", "https://nolimitz-backend-yfne.onrender.com").rstrip("/")
MAX_ALERTS_PER_MIN = int(os.getenv("TV_MAX_ALERTS_PER_MIN", "30"))
MAX_BODY = 4000

client_router = APIRouter(prefix="/api/client/telegram/tradingview", tags=["TradingView"])
hook_router = APIRouter(prefix="/api/tv", tags=["TradingView webhook"])

_hits = defaultdict(deque)          # secret -> recent alert times (per process; enough to stop a runaway alert)


class LicenceBody(BaseModel):
    license_key: str


def _url(secret: str) -> str:
    return f"{BASE_URL}/api/tv/{secret}"


def _tv_source(db: Session, licence_id: int):
    return db.query(TelegramSource).filter(
        TelegramSource.license_id == licence_id,
        TelegramSource.chat_id.like(PREFIX + "%"),
    ).first()


def _describe(src: TelegramSource) -> dict:
    secret = src.chat_id[len(PREFIX):]
    return {
        "connected": True,
        "source_id": src.id,
        "mode": src.mode,
        "enabled": bool(src.enabled),
        "webhook_url": _url(secret),
        "examples": {
            "text": "BUY XAUUSD SL 4050 TP 4100",
            "json": '{"action": "buy", "symbol": "XAUUSD", "sl": 4050, "tp": 4100}',
            "strategy": '{"action": "{{strategy.order.action}}", "symbol": "{{ticker}}"}',
            "close": "CLOSE XAUUSD",
        },
        "note": "Paste the URL into the alert's Webhook URL box and the example into its Message box. "
                "New connections start in Shadow mode: alerts are logged, no trades, until you switch to Live.",
    }


# ---------------------------------------------------------------------------- client endpoints
@client_router.get("/status")
def tv_status(license_key: str, db: Session = Depends(get_db)):
    lic = _licence(db, license_key)
    src = _tv_source(db, lic.id)
    if not src:
        return {"connected": False, "allowed": bool(plan_for(lic)["telegram"])}
    return _describe(src)


@client_router.post("/connect")
def tv_connect(data: LicenceBody, db: Session = Depends(get_db)):
    """Create (or return) this licence's TradingView source. Counts as one source on the plan."""
    lic = _licence(db, data.license_key)
    _require_telegram(lic)
    src = _tv_source(db, lic.id)
    if src:
        return _describe(src)
    cap = plan_for(lic)["max_sources"]
    n = db.query(TelegramSource).filter(TelegramSource.license_id == lic.id).count()
    if n >= cap:
        raise HTTPException(status_code=400,
                            detail=f"Your plan can follow {cap} sources and TradingView counts as one. "
                                   f"Remove a source to connect TradingView.")
    src = TelegramSource(license_id=lic.id, chat_id=PREFIX + secrets.token_urlsafe(24),
                         chat_title=TITLE, is_private=True, enabled=True, mode="SHADOW")
    db.add(src)
    db.commit()
    db.refresh(src)
    return _describe(src)


@client_router.post("/new-url")
def tv_new_url(data: LicenceBody, db: Session = Depends(get_db)):
    """Replace the webhook URL (if it leaked). The old URL stops working at once; settings are kept."""
    lic = _licence(db, data.license_key)
    src = _tv_source(db, lic.id)
    if not src:
        raise HTTPException(status_code=404, detail="TradingView is not connected")
    src.chat_id = PREFIX + secrets.token_urlsafe(24)
    db.commit()
    return _describe(src)


# ---------------------------------------------------------------------------- the webhook
def _to_text(raw: str) -> str:
    """TradingView sends the alert message as-is. JSON is turned into the text the parser reads."""
    raw = (raw or "").strip()[:MAX_BODY]
    if not raw.startswith("{"):
        return raw
    try:
        d = {str(k).lower(): v for k, v in json.loads(raw).items()}
    except Exception:
        return raw
    action = str(d.get("action") or d.get("side") or d.get("signal") or "").strip().upper()
    symbol = str(d.get("symbol") or d.get("ticker") or "").strip().upper().split(":")[-1]
    if action in ("EXIT", "CLOSE", "FLAT", "CLOSE_ALL"):
        return f"CLOSE {symbol}".strip()
    if action in ("LONG",):
        action = "BUY"
    if action in ("SHORT",):
        action = "SELL"
    parts = [f"{action} {symbol}"]
    price = d.get("price") or d.get("entry")
    if price not in (None, ""):
        parts[0] += f" @ {price}"             # market order; the price is the reference the parser and checks use
    if d.get("sl") not in (None, ""):
        parts.append(f"SL {d['sl']}")
    tps = [d[k] for k in ("tp", "tp1", "tp2", "tp3") if d.get(k) not in (None, "")]
    for i, tp in enumerate(tps, 1):
        parts.append(f"TP{i if len(tps) > 1 else ''} {tp}")
    return "\n".join(parts)


@hook_router.post("/{secret}")
async def tv_webhook(secret: str, request: Request, db: Session = Depends(get_db)):
    if len(secret) < 20:
        raise HTTPException(status_code=404, detail="Not found")
    src = db.query(TelegramSource).filter(TelegramSource.chat_id == PREFIX + secret).first()
    if not src:
        raise HTTPException(status_code=404, detail="Not found")

    now = time.time()
    q = _hits[secret]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= MAX_ALERTS_PER_MIN:
        logger.warning("TradingView alerts throttled for licence %s", src.license_id)
        return {"received": False, "outcome": "THROTTLED",
                "reason": f"more than {MAX_ALERTS_PER_MIN} alerts in a minute"}
    q.append(now)

    raw = (await request.body()).decode("utf-8", "replace")
    text = _to_text(raw)
    if not text:
        return {"received": False, "outcome": "EMPTY", "reason": "the alert message is empty"}
    # Same alert twice inside the same second is one signal (TradingView can double-fire).
    mid = f"tv-{int(now)}-{hashlib.sha1(text.encode()).hexdigest()[:10]}"
    data = IncomingSignal(license_id=src.license_id, chat_id=src.chat_id, message_id=mid, text=text,
                          sent_at=datetime.now(timezone.utc).isoformat())
    out = await run_in_threadpool(incoming_signal, data, True, db)   # the intake is synchronous DB work
    logger.info("TradingView alert licence=%s -> %s %s", src.license_id, out.get("outcome"), out.get("reason") or "")
    return out
