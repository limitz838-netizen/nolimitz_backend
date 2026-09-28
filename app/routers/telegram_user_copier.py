"""
================================================================================
  app/routers/telegram_user_copier.py  --  TELEGRAM COPIER (PER USER)
================================================================================

  Each user connects their OWN Telegram account and adds their OWN sources.
  A signal from a user's source reaches ONLY that user's MT5 account.

  ---- WHERE THIS JOINS THE EXISTING PIPELINE ---------------------------------

      Telegram listener  (VPS, one session per user)
            |
      POST /telegram-user/signal          <- this file
            |
      strict parser  (app/telegram/signal_parser.py)
            |
      risk gate  (this file, 19 checks, fail closed)
            |
      ONE CopierTradeEvent + ONE TradeExecution, for that ONE licence
            |
      existing copier fast lane  (shard = license_id % 7)   <- UNCHANGED
            |
      existing copier_executor._handle_open                 <- one line changed
            |
      user's MT5

  Nothing here places a trade. It writes the same row the master copier writes,
  and the lanes that already run do the rest. That is the whole design: the
  Telegram Copier is another signal source, not another execution engine.

  ---- THINGS LEARNED FROM READING THE EXECUTOR, THE HARD WAY ------------------

  1. `action` MUST BE LOWERCASE. copier_executor._send_market_order does
         is_buy = action == "buy"
     so an uppercase "BUY" evaluates False and places a SELL. A silent direction
     inversion with no error anywhere. app/routers/copier.py:609 lowercases for
     exactly this reason. _ACTION below is the only place this file produces the
     value, and there is a test for it.

  2. `event_type` and `status` are lowercase too: "open"/"close"/"modify",
     "pending".

  3. per_signal=1 on every row we write. Risk mode is a MULTIPLIER in
     _handle_open -- normal 1, medium 3, aggressive 5 -- so without this a single
     channel signal would open three positions on most accounts. NULL rows (the
     master copier's) are untouched.

  4. We must NOT reuse copier.create_execution_rows_for_event. It fans an event
     out to EVERY licence on the EA, which for a personal Telegram source would
     trade one user's channel on every account sharing their EA.

  5. The lane never reads CopierTradeEvent. The event row exists only to satisfy
     the NOT NULL foreign key on trade_executions.copier_event_id.

  6. MAX_OPEN_EVENT_AGE_SEC in the executor defaults to 60s. A signal that waits
     longer is skipped as stale. That is the right behaviour for someone else's
     signal, and the feed reports it honestly rather than hiding it.
================================================================================
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    ClientMT5Account,
    ClientSymbolSetting,
    CopierTradeEvent,
    ExpertAdvisor,
    License,
    TradeExecution,
)
from app.models_telegram import (
    TelegramAccount,
    TelegramSignalLog,
    TelegramSource,
)

from app.telegram_signal_parser import Refusal, parse_signal

try:
    from app.security_utils import encrypt_secret
except Exception:                                    # pragma: no cover
    def encrypt_secret(v):
        return v

logger = logging.getLogger("telegram_copier")

router = APIRouter(prefix="/api/client/telegram", tags=["Telegram Copier"])
worker_router = APIRouter(prefix="/telegram-user", tags=["Telegram Copier Worker"])


# ==============================================================================
# CONFIG
# ==============================================================================
# Master switch. Only the literal word "true" arms it, so "1", "yes" and "TRUE!"
# all mean off. Flipping this to false in Render stops every Telegram trade on
# the next request without touching the VPS.
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").strip().lower() == "true"

MAX_SOURCES_PER_LICENCE = int(os.getenv("TELEGRAM_MAX_SOURCES", "3"))
CONSENT_VERSION = os.getenv("TELEGRAM_CONSENT_VERSION", "2026-09-27")

# A session older than this with no listener heartbeat is reported offline.
LISTENER_STALE_SEC = int(os.getenv("TELEGRAM_LISTENER_STALE_SEC", "300"))

# How old a Telegram message may be, by its own send time, and still trade.
#
# Telethon's catch-up ("Got difference") re-delivers messages sent while the
# listener was down. Without this, restarting after an outage would trade
# yesterday's signals at today's price -- and every other staleness guard in the
# system misses it, because the execution row is created NOW and therefore looks
# fresh to the executor's MAX_OPEN_EVENT_AGE_SEC.
MAX_SIGNAL_AGE_SEC = int(os.getenv("TELEGRAM_MAX_SIGNAL_AGE_SEC", "180"))

# Shared secret for the listener intake. Same variable the copier worker routes
# use, so the VPS has it already.
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")

# Lowercase, because the executor compares against lowercase. See note 1.
_ACTION = {"BUY": "buy", "SELL": "sell"}

_VALID_MODES = ("SHADOW", "LIVE")


def require_worker_token(x_worker_token: str = Header(None)):
    if not WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="WORKER_TOKEN is not configured")
    if x_worker_token != WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid worker token")
    return True


# ==============================================================================
# THE SYMBOL MATCHER  --  borrowed, never copied
# ==============================================================================
# app/routers/copier.py carries the canonical matcher, and app/ai/copier_executor
# .py carries an identical copy with a "KEEP THESE IDENTICAL" warning on both. A
# THIRD copy here would be a third thing to keep in step, and the day they
# disagree this router creates rows the executor silently skips as "not enabled
# by client".
#
# So it is imported rather than reimplemented -- but LAZILY, inside a function.
# copier.py's own header records that a cross-router import once made a deploy
# hang before binding a port, so nothing here touches it at module import time.
# By the time any request arrives, main.py has already imported copier, so this
# is a sys.modules lookup and costs nothing.
def _matcher():
    from app.routers.copier import (
        _SYNONYM_LOOKUP, _canonical, find_symbol_setting,
    )
    return _SYNONYM_LOOKUP, _canonical, find_symbol_setting


def _now(db: Session) -> datetime:
    """The DATABASE clock.

    Received and executed are stamped on the VPS, parsed and validated on
    Render. Taken from local clocks these intervals would be meaningless and
    occasionally negative.
    """
    return db.execute(func.now()).scalar()


def _licence(db: Session, license_key: str) -> License:
    lic = db.query(License).filter(License.license_key == license_key).first()
    if not lic:
        raise HTTPException(status_code=400, detail="Invalid license key")
    return lic


def _own_source(db: Session, lic: License, source_id: int) -> TelegramSource:
    """A source is only ever reachable by the licence that owns it.

    Filtering on license_id here is what stops one user reading or editing
    another's sources by guessing an id.
    """
    src = db.query(TelegramSource).filter(
        TelegramSource.id == source_id,
        TelegramSource.license_id == lic.id,
    ).first()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    return src


# The MT5 order comment. The terminal's field is 31 characters and tolerates
# little beyond plain ASCII, while Telegram channel names routinely carry emoji,
# decorative unicode and double spaces -- "DELEON TRADING COMMUNITY [chart]" or
# "[trophy]OptimistFxTrader [trophy] [chart][phone]". Those are stripped rather
# than sent, because a comment the terminal mangles is worse than a short one.
ORDER_COMMENT_PREFIX = os.getenv("TELEGRAM_ORDER_PREFIX", "NOLIMITZ Ai")
_MT5_COMMENT_MAX = 31


def _order_comment(src) -> str:
    """"<prefix> <channel>", trimmed to what MT5 will actually carry.

    The prefix comes first on purpose: when a long channel name forces a
    truncation, what survives is the part that tells the user which system
    opened the trade. Losing the tail of a channel name is recoverable -- the
    signal feed has the full name -- but a comment that starts mid-word tells
    them nothing at all.
    """
    title = (src.chat_title or src.chat_id or "").strip()
    title = "".join(ch for ch in title if 32 <= ord(ch) < 127)
    title = re.sub(r"\s+", " ", title).strip(" -|")
    return f"{ORDER_COMMENT_PREFIX} {title}".strip()[:_MT5_COMMENT_MAX].strip()


# ==============================================================================
# RISK CAPS  --  mirrored from app/ai/copier_executor.py
# ==============================================================================
# KEEP THESE IDENTICAL to copier_executor's. The executor enforces max_open from
# its own copy, so a mapping that disagreed here would queue rows it then
# refuses -- three positions planned, two skipped, and a user watching a signal
# half-fill with no explanation. copier_executor.py cannot be imported on Render
# at all: it imports MetaTrader5, which exists only on the VPS.
#
# DEFAULT_RISK is "medium", not "normal". An account with no risk_level set gets
# three positions, not one.
RISK_CAPS = {"normal": 1, "medium": 3, "aggressive": 5}
DEFAULT_RISK = "medium"


def _risk_cap(account) -> int:
    level = (getattr(account, "risk_level", None) or DEFAULT_RISK).strip().lower()
    return RISK_CAPS.get(level, RISK_CAPS[DEFAULT_RISK])


def _tp_plan(take_profits, cap):
    """How many positions to open at each take profit: [(tp, count), ...].

    The cap is spread across the targets the channel actually published,
    earliest first, because the early targets are the ones that get hit. On
    medium (cap 3): three targets is one position each, two targets is two at
    TP1 and one at TP2, and a single target is all three there. A signal with
    no target at all still opens cap positions, carrying the stop and nothing
    else -- "TP WILL BE UPDATED" is a real message from a real channel.

    Targets beyond the cap are dropped rather than crowded in: on normal, a
    five-target signal is one position at TP1, not five at a fifth of the lot.
    """
    cap = max(1, int(cap))
    tps = list(take_profits or [])[:cap]
    if not tps:
        return [(None, cap)]
    base, extra = divmod(cap, len(tps))
    return [(tp, base + (1 if i < extra else 0)) for i, tp in enumerate(tps)]


def _signal_age_sec(sent_at: Optional[str]) -> Optional[float]:
    """Seconds between Telegram's send time and now, or None if unknowable.

    None means "do not judge", not "fresh" -- an older listener sends no
    timestamp, and inventing an age for it would refuse every signal it posts.
    A clock skew that puts the message slightly in the future clamps to 0
    rather than going negative and silently passing a later comparison.
    """
    if not sent_at:
        return None
    try:
        s = str(sent_at).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        logger.warning("unparseable sent_at from listener: %r", sent_at)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _mask_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    p = "".join(ch for ch in str(phone) if ch.isdigit() or ch == "+")
    if len(p) < 7:
        return "***"
    return p[:5] + "****" + p[-3:]


# ==============================================================================
# SCHEMAS
# ==============================================================================
class LinkRequest(BaseModel):
    license_key: str
    # Produced by the login that ran in the user's browser. This endpoint takes
    # the finished session and NOTHING else: it must never grow a `code` or
    # `password` field. If it ever does, the promise made on the consent screen
    # is broken.
    session_string: str
    tg_user_id: Optional[str] = None
    tg_username: Optional[str] = None
    phone: Optional[str] = None
    consent_version: Optional[str] = None


class SourceRequest(BaseModel):
    license_key: str
    chat_id: str
    chat_title: Optional[str] = None
    invite_url: Optional[str] = None
    is_private: bool = False


class SourceUpdate(BaseModel):
    license_key: str
    enabled: Optional[bool] = None
    mode: Optional[str] = None
    copy_buy: Optional[bool] = None
    copy_sell: Optional[bool] = None
    copy_sl: Optional[bool] = None
    copy_tp: Optional[bool] = None
    copy_closures: Optional[bool] = None
    allowed_symbols: Optional[str] = None
    max_lot: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    max_open_positions: Optional[int] = None
    trade_from: Optional[str] = None
    trade_to: Optional[str] = None
    require_sl: Optional[bool] = None


class IncomingSignal(BaseModel):
    """What the VPS listener posts. One message, one licence."""
    license_id: int
    chat_id: str
    message_id: str
    text: str
    reply_to_message_id: Optional[str] = None

    # When Telegram says the message was SENT, not when it reached us. Optional
    # so an older listener keeps working -- absent means the age check is
    # skipped, which is the behaviour that existed before this field.
    sent_at: Optional[str] = None


# ==============================================================================
# CLIENT ENDPOINTS -- connection
# ==============================================================================
@router.post("/link")
def link_telegram(data: LinkRequest, db: Session = Depends(get_db)):
    """Store the session produced by the browser login.

    The login code and 2FA password are entered directly with Telegram and never
    reach this server. What arrives here is the resulting session, which is
    encrypted at rest and never returned by any endpoint.
    """
    lic = _licence(db, data.license_key)

    if not data.session_string or len(data.session_string) < 20:
        raise HTTPException(status_code=400, detail="No Telegram session supplied")

    acc = db.query(TelegramAccount).filter(
        TelegramAccount.license_id == lic.id
    ).first()
    if not acc:
        acc = TelegramAccount(license_id=lic.id)
        db.add(acc)

    acc.session_encrypted = encrypt_secret(data.session_string)
    acc.tg_user_id = (data.tg_user_id or "")[:64] or None
    acc.tg_username = (data.tg_username or "")[:64] or None
    acc.phone_masked = _mask_phone(data.phone)
    acc.status = "LINKED"
    acc.consent_version = data.consent_version or CONSENT_VERSION
    acc.consent_at = _now(db)
    acc.linked_at = _now(db)
    db.commit()

    # Deliberately does not log the username or phone, and could not log the
    # session even by accident -- it is not in scope here.
    logger.info("telegram linked for licence %s", lic.id)

    return {"success": True, "status": "LINKED",
            "message": "Telegram connected. Add a signal source to begin."}


@router.get("/status")
def telegram_status(license_key: str, db: Session = Depends(get_db)):
    lic = _licence(db, license_key)
    acc = db.query(TelegramAccount).filter(
        TelegramAccount.license_id == lic.id
    ).first()

    if not acc:
        return {"connected": False, "status": "NOT_CONNECTED",
                "enabled_globally": TELEGRAM_ENABLED, "sources": 0}

    last = acc.last_seen_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = None if last is None else \
        (datetime.now(timezone.utc) - last).total_seconds()

    # Offline is stated out loud. Silence is how Send-to-MT5 died unnoticed for
    # six and a half weeks while the app kept promising trades would open.
    listener_online = age is not None and age < LISTENER_STALE_SEC

    n_sources = db.query(TelegramSource).filter(
        TelegramSource.license_id == lic.id
    ).count()

    return {
        "connected": acc.status == "LINKED",
        "status": acc.status,
        "tg_username": acc.tg_username,
        "phone_masked": acc.phone_masked,
        "linked_at": acc.linked_at.isoformat() if acc.linked_at else None,
        "listener_online": listener_online,
        "listener_age_seconds": int(age) if age is not None else None,
        "listener_message": (
            None if listener_online else
            "Telegram Copier offline - signals are not being monitored"),
        "enabled_globally": TELEGRAM_ENABLED,
        "sources": n_sources,
        "max_sources": MAX_SOURCES_PER_LICENCE,
        "consent_version": acc.consent_version,
    }
    # No session_encrypted. Not masked, not truncated -- absent.


@router.post("/disconnect")
def disconnect_telegram(data: dict, db: Session = Depends(get_db)):
    lic = _licence(db, (data or {}).get("license_key", ""))
    acc = db.query(TelegramAccount).filter(
        TelegramAccount.license_id == lic.id
    ).first()
    if not acc:
        return {"success": True, "status": "NOT_CONNECTED"}

    # The session is destroyed, not just flagged. A disconnect the user asked
    # for must actually remove our ability to read their Telegram.
    acc.session_encrypted = None
    acc.status = "DISCONNECTED"
    db.query(TelegramSource).filter(
        TelegramSource.license_id == lic.id
    ).update({"enabled": False}, synchronize_session=False)
    db.commit()
    logger.info("telegram disconnected for licence %s", lic.id)
    return {"success": True, "status": "DISCONNECTED",
            "message": "Telegram disconnected and all sources paused. "
                       "You can also remove the session from Telegram's own "
                       "Devices screen."}


# ==============================================================================
# CLIENT ENDPOINTS -- sources
# ==============================================================================
@router.post("/source")
def add_source(data: SourceRequest, db: Session = Depends(get_db)):
    lic = _licence(db, data.license_key)

    acc = db.query(TelegramAccount).filter(
        TelegramAccount.license_id == lic.id,
        TelegramAccount.status == "LINKED",
    ).first()
    if not acc:
        raise HTTPException(status_code=400,
                            detail="Connect your Telegram account first")

    n = db.query(TelegramSource).filter(TelegramSource.license_id == lic.id).count()
    if n >= MAX_SOURCES_PER_LICENCE:
        raise HTTPException(
            status_code=400,
            detail=f"You can follow {MAX_SOURCES_PER_LICENCE} sources. "
                   f"Remove one to add another.")

    chat_id = (data.chat_id or "").strip()
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")

    existing = db.query(TelegramSource).filter(
        TelegramSource.license_id == lic.id,
        TelegramSource.chat_id == chat_id,
    ).first()
    if existing:
        return {"success": True, "source_id": existing.id,
                "mode": existing.mode, "message": "Already added"}

    # SHADOW is not negotiable at creation. The user moves it to LIVE
    # themselves, once they have watched the feed.
    src = TelegramSource(
        license_id=lic.id,
        chat_id=chat_id,
        chat_title=(data.chat_title or "")[:120] or None,
        invite_url=(data.invite_url or "")[:300] or None,
        is_private=bool(data.is_private),
        enabled=True,
        mode="SHADOW",
    )
    db.add(src)
    db.commit()
    db.refresh(src)

    return {"success": True, "source_id": src.id, "mode": "SHADOW",
            "message": "Source added in Shadow mode. Signals will be parsed and "
                       "logged, and no trades placed, until you switch it to Live."}


@router.get("/sources")
def list_sources(license_key: str, db: Session = Depends(get_db)):
    lic = _licence(db, license_key)
    rows = db.query(TelegramSource).filter(
        TelegramSource.license_id == lic.id
    ).order_by(TelegramSource.id.asc()).all()

    out = []
    for s in rows:
        last = db.query(TelegramSignalLog).filter(
            TelegramSignalLog.source_id == s.id
        ).order_by(TelegramSignalLog.id.desc()).first()
        out.append({
            "id": s.id,
            "chat_id": s.chat_id,
            "chat_title": s.chat_title,
            "is_private": bool(s.is_private),
            "enabled": bool(s.enabled),
            "mode": s.mode,
            "copy_buy": bool(s.copy_buy), "copy_sell": bool(s.copy_sell),
            "copy_sl": bool(s.copy_sl), "copy_tp": bool(s.copy_tp),
            "copy_closures": bool(s.copy_closures),
            "allowed_symbols": s.allowed_symbols,
            "max_lot": s.max_lot,
            "max_trades_per_day": s.max_trades_per_day,
            "max_open_positions": s.max_open_positions,
            "trade_from": s.trade_from, "trade_to": s.trade_to,
            "require_sl": bool(s.require_sl),
            "trades_today": _trades_today(db, s),
            "last_signal_at": last.received_at.isoformat() if last and last.received_at else None,
            "last_outcome": last.outcome if last else None,
            "last_reason": last.reason if last else None,
        })
    return {"success": True, "sources": out,
            "max_sources": MAX_SOURCES_PER_LICENCE}


@router.patch("/source/{source_id}")
def update_source(source_id: int, data: SourceUpdate,
                  db: Session = Depends(get_db)):
    lic = _licence(db, data.license_key)
    src = _own_source(db, lic, source_id)

    if data.mode is not None:
        mode = data.mode.strip().upper()
        if mode not in _VALID_MODES:
            raise HTTPException(status_code=400, detail="mode must be SHADOW or LIVE")
        src.mode = mode

    for field in ("enabled", "copy_buy", "copy_sell", "copy_sl", "copy_tp",
                  "copy_closures", "require_sl"):
        v = getattr(data, field)
        if v is not None:
            setattr(src, field, bool(v))

    if data.allowed_symbols is not None:
        src.allowed_symbols = data.allowed_symbols.strip().upper() or None

    # Bounds, not suggestions. A user typing 500 into max_trades_per_day should
    # not be able to turn one channel into a margin call.
    if data.max_lot is not None:
        src.max_lot = max(0.01, min(float(data.max_lot), 100.0))
    if data.max_trades_per_day is not None:
        src.max_trades_per_day = max(1, min(int(data.max_trades_per_day), 50))
    if data.max_open_positions is not None:
        src.max_open_positions = max(1, min(int(data.max_open_positions), 20))

    for field in ("trade_from", "trade_to"):
        v = getattr(data, field)
        if v is not None:
            v = v.strip()
            if v and not _valid_hhmm(v):
                raise HTTPException(status_code=400,
                                    detail=f"{field} must be HH:MM")
            setattr(src, field, v or None)

    db.commit()
    return {"success": True, "source_id": src.id, "mode": src.mode}


@router.delete("/source/{source_id}")
def delete_source(source_id: int, license_key: str,
                  db: Session = Depends(get_db)):
    lic = _licence(db, license_key)
    src = _own_source(db, lic, source_id)
    db.delete(src)
    db.commit()
    return {"success": True, "deleted": source_id}


@router.get("/signals")
def list_signals(license_key: str, limit: int = 100,
                 db: Session = Depends(get_db)):
    """The feed. Every message seen and what became of it.

    This is what answers "it missed my trade" without anyone reading a log, and
    it is what makes Shadow mode a feature rather than a testing phase.
    """
    lic = _licence(db, license_key)
    limit = max(1, min(limit, 500))

    rows = db.query(TelegramSignalLog).filter(
        TelegramSignalLog.license_id == lic.id
    ).order_by(TelegramSignalLog.id.desc()).limit(limit).all()

    titles = {
        s.id: (s.chat_title or s.chat_id)
        for s in db.query(TelegramSource).filter(
            TelegramSource.license_id == lic.id).all()
    }

    # Read through to the execution rather than waiting for someone to copy
    # values onto the log. trade_executions is what the lane actually updates,
    # so it is the one place that knows a ticket exists -- and an earlier
    # version of this file left executed_at and mt5_ticket permanently null
    # because it assumed "the lane" would write them here. The lane has never
    # heard of this table.
    exec_ids = [r.execution_id for r in rows if r.execution_id]
    execs = {}
    if exec_ids:
        for e in db.query(TradeExecution).filter(
                TradeExecution.id.in_(exec_ids[:500])).all():
            execs[e.id] = e

    out = []
    for r in rows:
        parsed = {}
        if r.parsed_json:
            try:
                parsed = json.loads(r.parsed_json)
            except Exception:
                parsed = {}
        out.append({
            "id": r.id,
            "source": titles.get(r.source_id, r.chat_id),
            "raw_text": r.raw_text,
            "symbol": parsed.get("symbol_raw"),
            "direction": parsed.get("direction"),
            "entry_type": parsed.get("entry_type"),
            "entry_price": parsed.get("entry_price"),
            "stop_loss": parsed.get("stop_loss"),
            "take_profits": parsed.get("take_profits"),
            "outcome": r.outcome,
            "reason": r.reason,
            "received_at": r.received_at.isoformat() if r.received_at else None,
            "parsed_at": r.parsed_at.isoformat() if r.parsed_at else None,
            "validated_at": r.validated_at.isoformat() if r.validated_at else None,
            "executed_at": _executed_at(r, execs),
            "mt5_ticket": _ticket(r, execs),
            # What the lane is doing with it, and why it failed if it did. This
            # is where "No money" becomes visible to the user who owns the
            # account instead of only to whoever reads the worker log.
            "execution_status": _exec_field(r, execs, "status"),
            "execution_error": _exec_field(r, execs, "error_message"),
        })
    return {"success": True, "signals": out}


def _exec_field(row, execs, field):
    ex = execs.get(row.execution_id) if row.execution_id else None
    return getattr(ex, field, None) if ex is not None else None


def _ticket(row, execs):
    """The broker's ticket, from the execution; the log's own column is a
    fallback for anything written before the join existed."""
    ex = execs.get(row.execution_id) if row.execution_id else None
    if ex is not None and ex.client_ticket:
        return str(ex.client_ticket)
    return row.mt5_ticket


def _executed_at(row, execs):
    """When the order actually reached the broker.

    Keyed off the ticket, not off a status string: a ticket exists only if the
    broker accepted the order, whereas status spellings are the executor's
    business and would make this quietly wrong the day one of them changes.
    """
    if row.executed_at:
        return row.executed_at.isoformat()
    ex = execs.get(row.execution_id) if row.execution_id else None
    if ex is not None and ex.client_ticket and ex.updated_at:
        return ex.updated_at.isoformat()
    return None


# ==============================================================================
# HELPERS used by the gate
# ==============================================================================
def _valid_hhmm(v: str) -> bool:
    try:
        h, m = v.split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except Exception:
        return False


def _trades_today(db: Session, src: TelegramSource) -> int:
    """Signals from this source that produced a queued trade in the last 24h.

    Deliberately a rolling 24 hours rather than a calendar day: the broker's day
    boundary is a per-server question (see app/ai/day_pnl.py) and getting it
    wrong here would silently reset a user's daily cap at the wrong moment.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    return db.query(TelegramSignalLog).filter(
        TelegramSignalLog.source_id == src.id,
        TelegramSignalLog.outcome.in_(("QUEUED", "PLACED")),
        TelegramSignalLog.received_at >= since,
    ).count()


def _within_hours(src: TelegramSource) -> bool:
    if not src.trade_from or not src.trade_to:
        return True
    now = datetime.now(timezone.utc).strftime("%H:%M")
    a, b = src.trade_from, src.trade_to
    if a <= b:
        return a <= now <= b
    return now >= a or now <= b          # window crosses midnight


def _sl_tp_sane(direction: str, entry: Optional[float],
                sl: Optional[float], tp: Optional[float]) -> Optional[str]:
    """A BUY whose stop sits above entry is a typo in the channel, not a trade.

    Checked against the signal's own entry when it gave one. With no entry price
    there is nothing to compare against here, and the broker rejects a truly
    impossible level anyway.
    """
    if entry is None:
        return None
    if direction == "BUY":
        if sl is not None and sl >= entry:
            return f"BUY with stop loss {sl} at or above entry {entry}"
        if tp is not None and tp <= entry:
            return f"BUY with take profit {tp} at or below entry {entry}"
    else:
        if sl is not None and sl <= entry:
            return f"SELL with stop loss {sl} at or below entry {entry}"
        if tp is not None and tp >= entry:
            return f"SELL with take profit {tp} at or above entry {entry}"
    return None


# ==============================================================================
# THE PER-USER EXECUTION CREATOR
# ==============================================================================
def _create_executions(db: Session, lic: License, src: TelegramSource,
                       account: ClientMT5Account, setting: ClientSymbolSetting,
                       symbol_canon: str, direction: str,
                       sl: Optional[float], tps, entry: Optional[float],
                       master_ticket: str, event_type: str = "open"):
    """ONE row per take profit, for ONE licence. Returns (executions, plan).

    Deliberately not copier.create_execution_rows_for_event, which fans out to
    every licence on the EA. Here that would trade one user's private channel on
    every account that happens to share their Expert Advisor.

    The risk mode decides how many positions in total; the channel's targets
    decide how they are spread. Each row carries per_signal = its share, so the
    executor's existing multi-open path does the work and copier_executor.py --
    running live on seven lanes -- needs no change at all.

    Master tickets are suffixed with the target's index. They have to differ or
    the executor's ticket map would treat three positions as one, and closing
    TP1's position would be indistinguishable from closing TP3's.
    """
    ea = db.query(ExpertAdvisor).filter(ExpertAdvisor.id == lic.ea_id).first()
    ea_code = getattr(ea, "ea_code", None) or "TELEGRAM"

    label = _order_comment(src)
    plan = _tp_plan(tps if src.copy_tp else [], _risk_cap(account))

    executions = []
    for idx, (tp, count) in enumerate(plan, start=1):
        ticket = f"{master_ticket}-{idx}"
        tp_str = str(tp) if tp is not None else None
        sl_str = str(sl) if sl is not None else None

        event = CopierTradeEvent(
            source_admin_id=lic.admin_id,
            ea_id=lic.ea_id,
            ea_code=ea_code,
            event_type=event_type,
            master_ticket=ticket,
            symbol=symbol_canon,
            action=_ACTION[direction],      # lowercase. See note 1 in the header.
            lot_size=None,                  # never the provider's lot
            sl=sl_str,
            tp=tp_str,
            price=str(entry) if entry is not None else None,
            comment=label,
            status="pending",
        )
        db.add(event)
        db.flush()                          # need event.id for the FK

        execution = TradeExecution(
            copier_event_id=event.id,
            license_id=lic.id,
            ea_id=lic.ea_id,
            master_ticket=ticket,
            client_ticket=None,
            symbol=symbol_canon,
            action=_ACTION[direction],
            # THE USER'S OWN LOT, always. Never the number in the Telegram
            # message. Note it is the lot PER POSITION, so a three-target
            # signal is three times the exposure of a one-target signal.
            lot_size=str(setting.lot_size),
            sl=sl_str if src.copy_sl else None,
            tp=tp_str if src.copy_tp else None,
            price=str(entry) if entry is not None else None,
            comment=label,
            event_type=event_type,
            status="pending",
            per_signal=count,
        )
        db.add(execution)
        db.flush()
        executions.append(execution)

    return executions, plan


# ==============================================================================
# THE INTAKE  --  the listener posts here
# ==============================================================================
@worker_router.post("/signal")
def incoming_signal(data: IncomingSignal,
                    _: bool = Depends(require_worker_token),
                    db: Session = Depends(get_db)):
    """Parse, validate, and either queue a trade or record why not.

    Every path writes exactly one telegram_signal_log row, and that row's UNIQUE
    (license_id, chat_id, message_id) is the dedupe. Claim first, then act: if
    the insert conflicts, another delivery of the same message already owns it
    and this one stops.
    """
    lic = db.query(License).filter(License.id == data.license_id).first()
    if not lic:
        raise HTTPException(status_code=400, detail="Unknown licence")

    src = db.query(TelegramSource).filter(
        TelegramSource.license_id == lic.id,
        TelegramSource.chat_id == str(data.chat_id),
    ).first()
    if not src:
        # The listener sent a chat this licence does not follow. Not an error
        # worth 500-ing over, but worth refusing loudly rather than trading.
        return {"received": True, "outcome": "NOT_A_SOURCE",
                "reason": "this licence does not follow that chat"}

    # ---- claim the message ------------------------------------------------
    log = TelegramSignalLog(
        license_id=lic.id,
        source_id=src.id,
        chat_id=str(data.chat_id),
        message_id=str(data.message_id),
        raw_text=(data.text or "")[:4000],
        outcome="RECEIVED",
    )
    db.add(log)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"received": True, "outcome": "DUPLICATE",
                "reason": "already processed for this licence"}
    db.refresh(log)

    def finish(outcome: str, reason: str = "", **extra):
        log.outcome = outcome
        log.reason = (reason or None) and reason[:300]
        for k, v in extra.items():
            setattr(log, k, v)
        db.commit()
        return {"received": True, "outcome": outcome, "reason": reason,
                "log_id": log.id}

    # ---- parse -------------------------------------------------------------
    # The matcher is resolved OUTSIDE the try on purpose. It was inside once, and
    # a missing import came back to the user as "REFUSED_PARSE: parser error" on
    # every single signal -- a platform fault wearing a bad-signal label, which
    # is about the most expensive way to debug anything. Let it raise: the
    # listener sees a 500, retries, and the real traceback lands in the logs.
    #
    # The copier's own instrument vocabulary, so the parser accepts every symbol
    # the platform already understands and there is no second list to drift.
    synonyms, canonical, find_setting = _matcher()

    try:
        parsed = parse_signal(data.text, extra_symbols=synonyms.keys())
    except Refusal as r:
        log.parsed_at = _now(db)
        return finish("REFUSED_PARSE", f"{r.code}: {r.detail}".strip(": "))
    except Exception as e:
        # A genuine parser bug on one message. Refuse THAT message, keep
        # serving, and make it loud in the log -- never silent, never a trade.
        log.parsed_at = _now(db)
        logger.exception("parser raised on licence %s for message %s: %s",
                         lic.id, data.message_id, e)
        return finish("REFUSED_PARSE", f"parser error: {type(e).__name__}")

    log.parsed_at = _now(db)
    log.parsed_json = json.dumps(parsed)[:4000]

    if parsed["kind"] == "CLOSE":
        # Closures need the ticket maps of the ORIGINAL open, which the executor
        # finds by master_ticket. Implemented in the next step; refused rather
        # than half-done, because a close that silently does nothing leaves a
        # live position the user believes is shut.
        return finish("REFUSED_RISK", "closures_not_enabled_yet")

    direction = parsed["direction"]
    symbol_raw = parsed["symbol_raw"]
    symbol_canon = canonical(symbol_raw)

    # ---- the risk gate. In order. Fail closed. ----------------------------
    if not TELEGRAM_ENABLED:
        return finish("REFUSED_RISK", "telegram copier disabled globally")

    if not src.enabled:
        return finish("REFUSED_RISK", "source is paused")

    age = _signal_age_sec(data.sent_at)
    if age is not None and age > MAX_SIGNAL_AGE_SEC:
        # Refused, never queued late. A signal is a statement about a price at a
        # moment; acting on it an hour later is a different trade that nobody
        # chose. The row is still written, so the feed shows what was skipped.
        return finish("REFUSED_RISK",
                      f"signal was sent {int(age)}s ago "
                      f"(limit {MAX_SIGNAL_AGE_SEC}s)")

    exp = lic.expires_at
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if not lic.is_active or (exp and exp < datetime.now(timezone.utc)):
        return finish("REFUSED_RISK", "licence expired")

    account = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == lic.id,
        ClientMT5Account.is_active == True,        # noqa: E712
        ClientMT5Account.is_verified == True,      # noqa: E712
    ).first()
    if not account:
        return finish("REFUSED_RISK", "no verified MT5 account connected")

    if direction == "BUY" and not src.copy_buy:
        return finish("REFUSED_RISK", "BUY signals are switched off for this source")
    if direction == "SELL" and not src.copy_sell:
        return finish("REFUSED_RISK", "SELL signals are switched off for this source")

    if src.allowed_symbols:
        allowed = {canonical(s) for s in src.allowed_symbols.split(",") if s.strip()}
        if symbol_canon not in allowed:
            return finish("REFUSED_RISK",
                          f"{symbol_raw} is not in this source's allowed symbols")

    # The user's own enabled symbol, matched on the canonical key. This is also
    # where the lot comes from, and the executor refuses a symbol with no lot.
    setting = find_setting(db, lic.id, symbol_canon)
    if not setting:
        return finish("REFUSED_RISK",
                      f"{symbol_raw} is not enabled in your settings")
    try:
        lot = float(setting.lot_size or 0)
    except (TypeError, ValueError):
        lot = 0.0
    if lot <= 0:
        return finish("REFUSED_RISK",
                      f"no lot size set for {symbol_raw} in your settings")

    if parsed["entry_type"] in ("LIMIT", "STOP"):
        # Parsed correctly, and refused. NEVER converted to market: a BUY LIMIT
        # 3720 filled at market 3740 is a different trade, with the channel's
        # stop now far too close.
        return finish("REFUSED_RISK", "pending_orders_not_supported_v1")

    sl = parsed["stop_loss"] if src.copy_sl else None
    tps = parsed["take_profits"] if src.copy_tp else []

    if src.require_sl and sl is None:
        return finish("REFUSED_RISK", "this source requires a stop loss")

    # Every target, not just the first. Each one becomes its own position now,
    # so an inverted TP3 is a trade born losing exactly like an inverted TP1.
    for _t in (tps or [None]):
        bad = _sl_tp_sane(direction, parsed["entry_price"], sl, _t)
        if bad:
            return finish("REFUSED_RISK", bad)

    if not _within_hours(src):
        return finish("REFUSED_RISK",
                      f"outside this source's hours "
                      f"({src.trade_from}-{src.trade_to} UTC)")

    used = _trades_today(db, src)
    if used >= (src.max_trades_per_day or 5):
        return finish("REFUSED_RISK",
                      f"daily limit reached ({used} of {src.max_trades_per_day})")

    open_now = db.query(TradeExecution).filter(
        TradeExecution.license_id == lic.id,
        TradeExecution.master_ticket.like("TGU-%"),
        TradeExecution.status.in_(("pending", "processing", "executed")),
        TradeExecution.event_type == "open",
        TradeExecution.created_at >= datetime.now(timezone.utc) - timedelta(days=7),
    ).count()
    if open_now >= (src.max_open_positions or 2) * 4:
        # A coarse backstop only. The executor's own per-symbol position count is
        # the real ceiling; this stops a runaway channel flooding the queue.
        return finish("REFUSED_RISK",
                      f"too many recent Telegram trades queued ({open_now})")

    if src.max_lot is not None and lot > float(src.max_lot):
        return finish("REFUSED_RISK",
                      f"lot {lot} exceeds this source's maximum {src.max_lot}")

    log.validated_at = _now(db)

    # ---- shadow mode -------------------------------------------------------
    if (src.mode or "SHADOW").upper() != "LIVE":
        db.commit()
        return finish("SHADOW",
                      "Valid signal - no trade placed because this source is "
                      "in Shadow mode")

    # ---- queue it ----------------------------------------------------------
    master_ticket = f"TGU-{data.chat_id}-{data.message_id}-{lic.id}"
    try:
        executions, plan = _create_executions(
            db, lic, src, account, setting, symbol_canon, direction,
            sl, tps, parsed["entry_price"], master_ticket)
        # The first row is the anchor the feed links to. The rest are siblings
        # of the same message, found by the shared master_ticket prefix.
        log.execution_id = executions[0].id
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("could not queue telegram trade for licence %s: %s",
                     lic.id, e)
        return finish("REFUSED_RISK", f"could not queue: {e}")

    total = sum(n for _, n in plan)
    spread = ", ".join(
        f"{n}@{'no TP' if t is None else t}" for t, n in plan)
    logger.info("telegram QUEUED licence=%s %s %s lot=%s x%d (%s) rows=%s",
                lic.id, _ACTION[direction], symbol_canon, lot, total, spread,
                [e.id for e in executions])

    return finish("QUEUED",
                  f"queued for your MT5 ({_ACTION[direction]} {symbol_canon} "
                  f"lot {lot}, {total} position{'' if total == 1 else 's'}: "
                  f"{spread})")


@worker_router.post("/heartbeat")
def listener_heartbeat(data: dict,
                       _: bool = Depends(require_worker_token),
                       db: Session = Depends(get_db)):
    """The listener says a session is alive. Absence of this is what makes the
    dashboard say 'offline' instead of quietly implying everything is fine."""
    ids = (data or {}).get("license_ids") or []
    if not isinstance(ids, list) or not ids:
        return {"ok": True, "updated": 0}
    n = db.query(TelegramAccount).filter(
        TelegramAccount.license_id.in_([int(i) for i in ids][:500])
    ).update({"last_seen_at": func.now()}, synchronize_session=False)
    db.commit()
    return {"ok": True, "updated": n}


@worker_router.get("/sessions")
def listener_sessions(shard_index: int = 0, shard_count: int = 1,
                      _: bool = Depends(require_worker_token),
                      db: Session = Depends(get_db)):
    """What the listener shard should connect and watch.

    The ONLY endpoint that returns a session string, and it is guarded by the
    worker token. No browser reaches it and no client endpoint exposes it.
    """
    from app.security_utils import decrypt_secret

    accounts = db.query(TelegramAccount).filter(
        TelegramAccount.status == "LINKED",
        TelegramAccount.session_encrypted.isnot(None),
    ).all()

    out = []
    for a in accounts:
        if shard_count > 1 and (a.license_id % shard_count) != shard_index:
            continue
        chats = [
            s.chat_id for s in db.query(TelegramSource).filter(
                TelegramSource.license_id == a.license_id,
                TelegramSource.enabled == True,      # noqa: E712
            ).all()
        ]
        if not chats:
            continue
        try:
            session = decrypt_secret(a.session_encrypted)
        except Exception:
            logger.warning("could not decrypt session for licence %s", a.license_id)
            continue
        out.append({"license_id": a.license_id, "session": session,
                    "chat_ids": chats})
    return {"sessions": out}


@worker_router.post("/session-status")
def set_session_status(data: dict,
                       _: bool = Depends(require_worker_token),
                       db: Session = Depends(get_db)):
    """The listener reports a session it can no longer use.

    REVOKED means the user logged it out; the listener stops retrying, because
    hammering a dead session achieves nothing but drawing Telegram's attention.
    """
    lid = (data or {}).get("license_id")
    status = str((data or {}).get("status", "")).upper()
    if status not in ("LINKED", "DISCONNECTED", "LIMITED", "REVOKED"):
        raise HTTPException(status_code=400, detail="bad status")
    acc = db.query(TelegramAccount).filter(
        TelegramAccount.license_id == int(lid)
    ).first()
    if not acc:
        return {"ok": False}
    acc.status = status
    if status in ("REVOKED", "DISCONNECTED"):
        acc.session_encrypted = None
    db.commit()
    logger.info("telegram session for licence %s -> %s", lid, status)
    return {"ok": True, "status": status}


@router.get("/health")
def telegram_health(db: Session = Depends(get_db)):
    """Safe to open in a browser. Names no user and returns no secret."""
    return {
        "enabled": TELEGRAM_ENABLED,
        "worker_token_configured": bool(WORKER_TOKEN),
        "max_sources_per_licence": MAX_SOURCES_PER_LICENCE,
        "consent_version": CONSENT_VERSION,
        "linked_accounts": db.query(TelegramAccount).filter(
            TelegramAccount.status == "LINKED").count(),
        "live_sources": db.query(TelegramSource).filter(
            TelegramSource.mode == "LIVE",
            TelegramSource.enabled == True).count(),      # noqa: E712
        "shadow_sources": db.query(TelegramSource).filter(
            TelegramSource.mode == "SHADOW").count(),
    }
