"""
================================================================================
  COPIER ROUTER  —  master account → licensed client accounts
================================================================================

  ── FIXES IN THIS REVISION ───────────────────────────────────────────────────
  1. SYMBOLS ARE NO LONGER HARDCODED. get_symbol_aliases() held a fixed map of
     six instruments and anything else fell through to an exact-name match. So
     an admin could add NAS100 or Volatility 75 Index to their licence keys and
     every fan-out would skip with "symbol_not_enabled" the moment a client's
     broker spelled it differently. Replaced with _canonical(), the same
     matcher app/ai/copier_executor.py uses. Any instrument now works without
     being listed anywhere.

     KEEP THE MATCHER BLOCK IDENTICAL to the one in copier_executor.py. If they
     disagree, this router creates rows the executor silently skips as "not
     enabled by client".

  2. MODIFY now gets the same open-position filter as CLOSE. Previously only
     `event_type == "close"` was checked, so every SL/TP change on the master
     fanned out to EVERY licence on the EA. Measured 31 Aug: 150 of 258 copier
     events in one hour were modifies, each costing ~4s of MT5 login only for
     _handle_modify to return "no open trades mapped to this master ticket".

  3. Added a race guard to that filter. A close or modify can legitimately
     arrive before the client's own OPEN has executed — on a fast scalp the
     open is still queued, so no ticket map exists yet. Skipping on that basis
     would strand the client in a position the master has already exited. The
     guard defers to the slow path whenever an open is still in flight. This
     hole existed in the close branch before this revision too.

  ── PREVIOUS REVISION ────────────────────────────────────────────────────────
  1. The CLOSE branch uses `TradeTicketMap.is_closed == False`. The real
     columns are id, license_id, execution_id, master_ticket, client_ticket,
     symbol, is_closed, closed_by_client, closed_at, last_error, created_at.
     There is no is_open, no ea_id and no child_ticket_index — an earlier
     revision assumed otherwise and 500'd every close.

     BEWARE: a SECOND table named `trade_ticket_maps` exists in the same
     database with exactly those legacy columns. It holds 0 rows and nothing
     writes to it. The live table is `ticket_maps` — always confirm against
     TradeTicketMap.__tablename__ rather than the name that reads better.
  2. Removed `import asyncio` and the execution_dispatcher import, which
     dragged MetaApiService into every request through the import chain.
  3. `/copier/executions/{id}/account` used mt5.mt_login / mt5.mt_server, which
     are not columns on ClientMT5Account (they are login / server).
  4. Machine-only routes require the same X-Worker-Token used by /worker/*.

  ── HOW EXECUTION ACTUALLY HAPPENS ───────────────────────────────────────────
  This router only CREATES pending TradeExecution rows. It does not dispatch.
  `app/ai/copier_executor.py`, running inside the copier fast lane, polls for
  `status == "pending"` and places the trades on each client's terminal.
  Creating the row IS the dispatch.
================================================================================
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import decode_access_token
from app.database import get_db
from app.models import (
    Admin,
    ClientMT5Account,
    ClientSymbolSetting,
    CopierTradeEvent,
    ExpertAdvisor,
    License,
    TradeExecution,
    TradeTicketMap,
)
from app.schemas import (
    CopierCloseTradeRequest,
    CopierModifyTradeRequest,
    CopierOpenTradeRequest,
    CreateExecutionsResponse,
    ExecutionUpdateRequest,
    TradeExecutionItem,
    TradeTicketMapItem,
)
from app.security import decrypt_text

router = APIRouter(prefix="/copier", tags=["Copier"])


# =========================
# WORKER AUTH
# =========================
# Defined locally rather than imported from app.routers.mt5_workers. That
# cross-router import was the only structural change in an earlier revision,
# and the deploy hung before binding a port — so every router stays
# self-contained. The symbol matcher below is duplicated for the same reason.
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")


def require_worker_token(x_worker_token: str = Header(None)):
    """Shared secret for routes no browser should ever reach."""
    if not WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="WORKER_TOKEN is not configured")
    if x_worker_token != WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid worker token")
    return True


# =========================
# HELPERS
# =========================
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    return authorization.split(" ", 1)[1].strip()


def get_current_admin(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> Admin:
    token = require_bearer_token(authorization)

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    admin_id = payload.get("admin_id")
    if not admin_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    admin = db.query(Admin).filter(Admin.id == admin_id).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    if not admin.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    if admin.role != "super_admin" and not admin.is_approved:
        raise HTTPException(status_code=403, detail="Your account is pending approval")

    return admin


def get_ea_by_code_for_admin(ea_code: str, current_admin: Admin, db: Session) -> ExpertAdvisor:
    normalized_ea_code = ea_code.strip()

    ea = db.query(ExpertAdvisor).filter(
        ExpertAdvisor.ea_code == normalized_ea_code,
        ExpertAdvisor.admin_id == current_admin.id,
        ExpertAdvisor.is_active == True,
    ).first()

    if not ea:
        raise HTTPException(status_code=404, detail="EA code not found or inactive")

    return ea


def get_ea_by_id_for_admin(ea_id: int, current_admin: Admin, db: Session) -> ExpertAdvisor:
    """Ownership check lives here — this is what keeps one tenant's master
    trades from ever reaching another tenant's licence holders."""
    ea = db.query(ExpertAdvisor).filter(
        ExpertAdvisor.id == ea_id,
        ExpertAdvisor.admin_id == current_admin.id,
        ExpertAdvisor.is_active == True,
    ).first()

    if not ea:
        raise HTTPException(status_code=404, detail="EA not found or inactive")

    return ea


def serialize_execution(row: TradeExecution) -> TradeExecutionItem:
    return TradeExecutionItem(
        id=row.id,
        copier_event_id=row.copier_event_id,
        license_id=row.license_id,
        ea_id=row.ea_id,
        master_ticket=row.master_ticket,
        client_ticket=row.client_ticket,
        symbol=row.symbol,
        action=row.action,
        lot_size=row.lot_size,
        sl=row.sl,
        tp=row.tp,
        price=row.price,
        comment=row.comment,
        event_type=row.event_type,
        status=row.status,
        error_message=row.error_message,
        created_at=row.created_at,
    )


def serialize_ticket_map(row: TradeTicketMap) -> Dict[str, Any]:
    """Plain dict rather than TradeTicketMapItem.

    That schema expects ea_id, child_ticket_index, action, is_open,
    manually_closed and opened_at — none of which exist on this table — so
    every ticket-map endpoint raised AttributeError. Returning the real columns
    keeps them usable without touching schemas.py.
    """
    return {
        "id": row.id,
        "license_id": row.license_id,
        "execution_id": row.execution_id,
        "master_ticket": row.master_ticket,
        "client_ticket": row.client_ticket,
        "symbol": row.symbol,
        "is_closed": row.is_closed,
        "closed_by_client": row.closed_by_client,
        "closed_at": row.closed_at,
        "last_error": row.last_error,
        "created_at": row.created_at,
    }


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


# ==============================================================================
# SYMBOL MATCHING  —  any instrument, any broker naming
# ==============================================================================
# MUST STAY IDENTICAL to the block in app/ai/copier_executor.py.
#
# TWO BUGS THIS DESIGN AVOIDS:
#  1. COLLIDING SYNTHETICS. Truncating long names to their first 6 letters made
#     "Volatility 75 Index", "Volatility 25 Index" and "Volatility 100 Index"
#     all become "VOLATI" — a master trade on V75 would have matched a client's
#     V25 setting and opened the wrong instrument. Anything containing a digit
#     keeps its full identity, because in synthetics the number IS the symbol.
#  2. "GOLD BASKET" BECAME "GOLD". The letter-trimming loop chewed
#     "GOLDBASKET" down to "GOLD" and matched it to XAUUSD. Trimming is now
#     applied only to single-token names (XAUUSDc, GOLDmicro, EURUSDz); a name
#     containing a space is descriptive, not decorated, and is kept whole.
#
# HOW A SYMBOL IS MATCHED:
#   1. strip separator decoration (. _ - # /) and collapse spaces
#   2. exact synonym hit wins
#   3. contains a digit, or the original had a space  -> keep whole
#   4. otherwise treat as an FX-style token: trim trailing broker letters,
#      checking synonyms at every length, then fall back to first 6 letters

_DERIV_VOL = (10, 25, 50, 75, 100)
_DERIV_VOL_1S = (10, 25, 50, 75, 100, 150, 200, 250, 300)
_DERIV_JUMP = (10, 25, 50, 75, 100)
_DERIV_BOOM_CRASH = (300, 500, 1000)

_NOISE_SUFFIX = {
    "CASH", "SPOT", "IDX", "INDEX", "RAW", "ECN", "PRO", "STD", "STP", "MICRO",
    "MINI", "M", "C", "Z", "E", "R", "X", "FT", "FUT", "ROLL", "RFD", "SB",
}

_SYNONYM_GROUPS = [
    {"XAUUSD", "GOLD", "GOLDUSD", "GOLDSPOT"},
    {"XAGUSD", "SILVER", "SILVERUSD"},
    {"XCUUSD", "COPPER"},
    {"XPTUSD", "PLATINUM"},
    {"BTCUSD", "BTCUSDT", "BITCOIN"},
    {"ETHUSD", "ETHUSDT", "ETHEREUM"},
    {"US30", "DJ30", "DOW", "DOW30", "WS30", "USA30", "DJI30", "YM", "US30CASH"},
    {"NAS100", "US100", "USTEC", "NDX100", "USA100", "TECH100", "NQ100", "NQ", "USTECH100"},
    {"SPX500", "US500", "SP500", "USA500", "ES", "SPX"},
    {"USOIL", "WTI", "CRUDE", "XTIUSD", "USOUSD", "WTIUSD", "CL", "CRUDEOIL"},
    {"UKOIL", "BRENT", "XBRUSD", "UKOUSD", "BRENTUSD"},
    {"NATGAS", "NGAS", "XNGUSD", "NATURALGAS"},
    {"GER40", "DE40", "DAX40", "GER30", "DE30", "DAX", "GERMANY40", "GERMANY30"},
    {"UK100", "FTSE100", "FTSE", "GB100", "BRITAIN100"},
    {"JP225", "JPN225", "NIKKEI", "N225", "JAPAN225"},
    {"FRA40", "CAC40", "FR40", "FRANCE40"},
    {"AUS200", "AU200", "ASX200", "AUSTRALIA200"},
    {"HK50", "HKG33", "HSI", "HONGKONG50"},
    {"EU50", "STOXX50", "EUSTX50", "ESX50", "EUROPE50"},
    {"US2000", "RUSSELL2000", "RUT", "USA2000"},
    {"STEPINDEX", "STEP"},
    {"MULTISTEPINDEX", "MULTISTEP"},
]

for _n in _DERIV_VOL:
    _SYNONYM_GROUPS.append({f"VOLATILITY{_n}INDEX", f"VOLATILITY{_n}", f"V{_n}", f"VIX{_n}", f"VOL{_n}"})
for _n in _DERIV_VOL_1S:
    _SYNONYM_GROUPS.append({f"VOLATILITY{_n}(1S)INDEX", f"VOLATILITY{_n}(1S)", f"V{_n}(1S)", f"VOL{_n}(1S)"})
for _n in _DERIV_JUMP:
    _SYNONYM_GROUPS.append({f"JUMP{_n}INDEX", f"JUMP{_n}", f"J{_n}"})
for _n in _DERIV_BOOM_CRASH:
    _SYNONYM_GROUPS.append({f"BOOM{_n}INDEX", f"BOOM{_n}", f"B{_n}"})
    _SYNONYM_GROUPS.append({f"CRASH{_n}INDEX", f"CRASH{_n}", f"C{_n}"})
for _n in (100, 200):
    _SYNONYM_GROUPS.append({f"RANGEBREAK{_n}INDEX", f"RANGEBREAK{_n}", f"RB{_n}"})
for _n in (200, 500):
    _SYNONYM_GROUPS.append({f"STEP{_n}INDEX", f"STEP{_n}"})
for _n in (10, 20, 30):
    _SYNONYM_GROUPS.append({f"DRIFTSWITCHINDEX{_n}", f"DRIFTSWITCH{_n}", f"DSI{_n}"})
for _n in (600, 900, 1500):
    for _d in ("UP", "DOWN"):
        _SYNONYM_GROUPS.append({f"DEX{_n}{_d}INDEX", f"DEX{_n}{_d}"})
for _b in ("AUD", "EUR", "GBP", "USD", "GOLD"):
    _SYNONYM_GROUPS.append({f"{_b}BASKET", f"{_b}BASKETINDEX"})

_SYNONYM_LOOKUP: Dict[str, str] = {}
for _group in _SYNONYM_GROUPS:
    _canon_name = sorted(_group)[0]
    for _name in _group:
        _SYNONYM_LOOKUP[_name] = _canon_name


def _strip_decoration(sym: str) -> str:
    s = (sym or "").upper().strip()
    s = s.lstrip(".#_-/ ")
    parts, buf = [], ""
    for ch in s:
        if ch in "._-#/ ":
            if buf:
                parts.append(buf); buf = ""
        else:
            buf += ch
    if buf:
        parts.append(buf)
    if not parts:
        return ""
    while len(parts) > 1 and parts[-1] in _NOISE_SUFFIX:
        parts.pop()
    return "".join(parts)


def _canonical(sym: str) -> str:
    raw = (sym or "").upper().strip()
    s = _strip_decoration(raw)
    if not s:
        return ""
    if s in _SYNONYM_LOOKUP:
        return _SYNONYM_LOOKUP[s]

    if any(ch.isdigit() for ch in s):
        tail = ""
        while s and s[-1].isalpha():
            tail = s[-1] + tail
            s = s[:-1]
            if s in _SYNONYM_LOOKUP:
                return _SYNONYM_LOOKUP[s]
            if tail in _NOISE_SUFFIX and s and s[-1].isdigit():
                break
        return _strip_decoration(raw)

    if " " in raw:
        return s

    fx_guess = None
    for cut in range(1, 7):
        if len(s) - cut < 3:
            break
        cand = s[:len(s) - cut]
        if cand in _SYNONYM_LOOKUP:
            return _SYNONYM_LOOKUP[cand]
        if fx_guess is None and len(cand) == 6 and cand.isalpha():
            fx_guess = cand
    if fx_guess:
        return fx_guess
    if len(s) > 6:
        return s[:6]
    return s


def find_symbol_setting(db: Session, license_id: int, master_symbol: str):
    """The client's enabled setting for this instrument, whatever either side
    calls it.

    Replaces the old alias-map lookup. The client's rows are read once and
    compared on the canonical key, so a symbol an admin added last week works
    with no code change — which is the whole point of admin-managed symbols.
    """
    want = _canonical(master_symbol)
    if not want:
        return None

    rows = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == license_id,
        ClientSymbolSetting.enabled == True,  # noqa: E712
    ).all()

    for setting in rows:
        if _canonical(getattr(setting, "symbol_name", "")) == want:
            return setting
    return None


def create_execution_rows_for_event(event: CopierTradeEvent, db: Session) -> List[TradeExecution]:
    """Fan the master event out to every eligible licence on this EA.

    Scoped by ea_id, and the EA's ownership was already checked by
    get_ea_by_id_for_admin, so this cannot cross tenants.
    """
    licenses = db.query(License).filter(
        License.ea_id == event.ea_id,
        License.is_active == True,
    ).all()

    created_rows: List[TradeExecution] = []

    for license_row in licenses:

        mt5_account = db.query(ClientMT5Account).filter(
            ClientMT5Account.license_id == license_row.id,
            ClientMT5Account.is_active == True,
            ClientMT5Account.is_verified == True,
        ).first()

        if not mt5_account:
            print(f"[SKIP] license={license_row.id} reason=no_active_verified_mt5")
            continue

        # Canonical match — works for any symbol the admin has added, not just
        # a fixed list of six.
        symbol_setting = find_symbol_setting(db, license_row.id, event.symbol)

        if not symbol_setting:
            print(f"[SKIP] license={license_row.id} reason=symbol_not_enabled "
                  f"symbol={event.symbol}")
            continue

        # Direction preference applies to OPEN only — a client who only takes
        # buys must still be able to close and modify what they already hold.
        if event.event_type == "open":
            client_direction = (symbol_setting.trade_direction or "both").lower()
            event_action = (event.action or "").lower()

            if client_direction != "both" and client_direction != event_action:
                print(f"[SKIP] license={license_row.id} reason=direction_blocked")
                continue

        # For CLOSE and MODIFY, only queue clients that actually hold an open
        # copy of this master ticket.
        #
        # MODIFY was previously excluded from this check, which is why a
        # trailing stop fanned out to every licence on the EA — 150 of 258
        # events in one measured hour, most resolving to "no open trades mapped
        # to this master ticket" only after paying a ~4s MT5 login.
        if event.event_type in ("close", "modify"):
            open_map = db.query(TradeTicketMap).filter(
                TradeTicketMap.license_id == license_row.id,
                TradeTicketMap.master_ticket == event.master_ticket,
                TradeTicketMap.is_closed == False,  # noqa: E712
            ).first()

            # A close or modify can legitimately arrive before this client's
            # own OPEN has executed — on a fast scalp the open is still sitting
            # in the queue, so no ticket map exists yet. Dropping the close on
            # that basis would leave the client holding a position the master
            # has already exited, with nothing left to close it. Whenever an
            # open is still in flight, let the row through and let the executor
            # decide with the terminal in hand.
            open_in_flight = db.query(TradeExecution).filter(
                TradeExecution.license_id == license_row.id,
                TradeExecution.master_ticket == event.master_ticket,
                TradeExecution.event_type == "open",
                TradeExecution.status.in_(("pending", "processing")),
            ).first()

            if not open_map and not open_in_flight:
                print(f"[SKIP] license={license_row.id} reason=no_open_position")
                continue

        execution = TradeExecution(
            copier_event_id=event.id,
            license_id=license_row.id,
            ea_id=event.ea_id,
            master_ticket=event.master_ticket,
            client_ticket=None,
            symbol=event.symbol,
            action=event.action,
            # The CLIENT's lot size, never the master's. This is what makes one
            # master trade safe across accounts of wildly different sizes.
            lot_size=symbol_setting.lot_size,
            sl=event.sl,
            tp=event.tp,
            price=event.price,
            comment=event.comment,
            event_type=event.event_type,
            status="pending",
            error_message=None,
        )

        db.add(execution)
        created_rows.append(execution)

        print(f"[EXEC CREATED] license={license_row.id} symbol={event.symbol}")

    db.commit()

    for row in created_rows:
        db.refresh(row)

    return created_rows


def create_event_and_executions(
    *,
    db: Session,
    current_admin: Admin,
    ea: ExpertAdvisor,
    event_type: str,
    master_ticket: str,
    symbol: str,
    action: str | None,
    sl: str | None,
    tp: str | None,
    price: str | None,
    comment: str | None,
) -> CreateExecutionsResponse:
    # Stamp the admin's own EA name onto the trade so clients see the brand
    # they subscribed to, not the master's raw comment. MT5 truncates at 31.
    ea_label = (getattr(ea, "name", None) or ea.ea_code or "Copier")[:31]
    event = CopierTradeEvent(
        source_admin_id=current_admin.id,
        ea_id=ea.id,
        ea_code=ea.ea_code,
        event_type=event_type,
        master_ticket=master_ticket.strip(),
        symbol=normalize_symbol(symbol),
        action=action,
        lot_size=None,
        sl=sl,
        tp=tp,
        price=price,
        comment=ea_label,
        status="pending",
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    execution_rows = create_execution_rows_for_event(event, db)

    return CreateExecutionsResponse(
        message=f"Copier {event_type} event created and routed successfully",
        event_id=event.id,
        total_created=len(execution_rows),
        executions=[serialize_execution(row) for row in execution_rows],
    )


# =========================
# COPIER EVENTS
# =========================
@router.post("/open", response_model=CreateExecutionsResponse)
def copier_open_trade(
    payload: CopierOpenTradeRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    ea = get_ea_by_id_for_admin(payload.ea_id, current_admin, db)

    action = payload.action.lower().strip()
    if action not in ["buy", "sell"]:
        raise HTTPException(status_code=400, detail="action must be buy or sell")

    return create_event_and_executions(
        db=db,
        current_admin=current_admin,
        ea=ea,
        event_type="open",
        master_ticket=payload.master_ticket,
        symbol=payload.symbol,
        action=action,
        sl=payload.sl,
        tp=payload.tp,
        price=payload.price,
        comment=payload.comment,
    )


@router.post("/modify", response_model=CreateExecutionsResponse)
def copier_modify_trade(
    payload: CopierModifyTradeRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    ea = get_ea_by_id_for_admin(payload.ea_id, current_admin, db)

    return create_event_and_executions(
        db=db,
        current_admin=current_admin,
        ea=ea,
        event_type="modify",
        master_ticket=payload.master_ticket,
        symbol=payload.symbol,
        action=None,
        sl=payload.sl,
        tp=payload.tp,
        price=payload.price,
        comment=payload.comment,
    )


@router.post("/close", response_model=CreateExecutionsResponse)
def copier_close_trade(
    payload: CopierCloseTradeRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    ea = get_ea_by_id_for_admin(payload.ea_id, current_admin, db)

    return create_event_and_executions(
        db=db,
        current_admin=current_admin,
        ea=ea,
        event_type="close",
        master_ticket=payload.master_ticket,
        symbol=payload.symbol,
        action=None,
        sl=None,
        tp=None,
        price=None,
        comment=payload.comment,
    )


# =========================
# EXECUTIONS
# =========================
@router.get("/executions", response_model=List[TradeExecutionItem])
def list_my_executions(
    current_admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    rows = db.query(TradeExecution).filter(
        TradeExecution.ea_id.in_(
            db.query(ExpertAdvisor.id).filter(ExpertAdvisor.admin_id == current_admin.id)
        )
    ).order_by(TradeExecution.id.desc()).all()

    return [serialize_execution(row) for row in rows]


@router.post("/executions/claim", response_model=List[TradeExecutionItem])
def claim_pending_executions(
    limit: int = Query(default=10, ge=1, le=100),
    _: bool = Depends(require_worker_token),
    db: Session = Depends(get_db),
):
    """Legacy HTTP claim path, kept for compatibility.

    The live executor does NOT use this — it reads pending rows straight from
    the database inside the copier fast lane. Left behind a worker token so an
    anonymous caller cannot claim rows and silently stop trades reaching
    clients.
    """
    rows = db.query(TradeExecution).filter(
        TradeExecution.status == "pending"
    ).order_by(TradeExecution.id.asc()).limit(limit).all()

    for row in rows:
        row.status = "processing"

    db.commit()

    for row in rows:
        db.refresh(row)

    return [serialize_execution(row) for row in rows]


@router.post("/executions/{execution_id}/update")
def update_execution_result(
    execution_id: int,
    payload: ExecutionUpdateRequest,
    _: bool = Depends(require_worker_token),
    db: Session = Depends(get_db),
):
    row = db.query(TradeExecution).filter(
        TradeExecution.id == execution_id
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="Execution row not found")

    row.status = payload.status
    row.client_ticket = payload.client_ticket
    row.error_message = payload.error_message
    db.commit()
    db.refresh(row)

    return {
        "message": "Execution updated successfully",
        "execution_id": row.id,
        "status": row.status,
        "client_ticket": row.client_ticket,
        "error_message": row.error_message,
    }


@router.get("/executions/{execution_id}/account")
def get_execution_account(
    execution_id: int,
    _: bool = Depends(require_worker_token),
    db: Session = Depends(get_db),
):
    """Account details for an execution.

    Column names corrected: ClientMT5Account uses login / server, not
    mt_login / mt_server, so this used to 500. The MetaAPI id is returned when
    present but is no longer required — MetaAPI was dropped and demanding it
    made this endpoint fail for every account.

    The password is deliberately not returned; the executor reads it from the
    database on the same host and never over the network.
    """
    row = db.query(TradeExecution).filter(
        TradeExecution.id == execution_id
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="Execution row not found")

    license_row = db.query(License).filter(
        License.id == row.license_id
    ).first()

    if not license_row:
        raise HTTPException(status_code=404, detail="License not found for this execution")

    account = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == row.license_id
    ).first()

    if not account:
        raise HTTPException(status_code=404, detail="No MT5 account found for this execution")

    return {
        "execution_id": row.id,
        "license_id": row.license_id,
        "license_key": license_row.license_key,
        "login": account.login,
        "server": account.server,
        "metaapi_account_id": getattr(account, "metaapi_account_id", None),
        "is_active": account.is_active,
        "is_verified": account.is_verified,
    }


# =========================
# TICKET MAPS
# =========================
@router.get("/ticket-maps")
def list_ticket_maps(
    db: Session = Depends(get_db),
):
    rows = db.query(TradeTicketMap).order_by(TradeTicketMap.id.desc()).all()
    return [serialize_ticket_map(row) for row in rows]


@router.get("/ticket-maps/by-execution/{execution_id}")
def get_ticket_maps_for_execution(
    execution_id: int,
    db: Session = Depends(get_db),
):
    execution = db.query(TradeExecution).filter(
        TradeExecution.id == execution_id
    ).first()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    rows = db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == execution.license_id,
        TradeTicketMap.master_ticket == execution.master_ticket,
    ).order_by(TradeTicketMap.id.asc()).all()

    if not rows:
        raise HTTPException(status_code=404, detail="Ticket map not found")

    return [serialize_ticket_map(row) for row in rows]


@router.get("/ticket-maps/by-keys")
def get_ticket_maps_by_keys(
    license_id: int,
    ea_id: int,
    master_ticket: str,
    db: Session = Depends(get_db),
):
    rows = db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == license_id,
        TradeTicketMap.master_ticket == master_ticket,
    ).order_by(TradeTicketMap.id.asc()).all()

    if not rows:
        raise HTTPException(status_code=404, detail="Ticket map not found")

    return [serialize_ticket_map(row) for row in rows]


@router.post("/ticket-maps/upsert")
def upsert_ticket_map(
    payload: Dict[str, Any],
    _: bool = Depends(require_worker_token),
    db: Session = Depends(get_db),
):
    """Keyed on client_ticket. Without child_ticket_index, the client ticket is
    what distinguishes the children of a multi-trade fan-out."""
    for field in ["license_id", "master_ticket", "client_ticket", "symbol"]:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")

    license_id = int(payload["license_id"])
    master_ticket = str(payload["master_ticket"]).strip()
    client_ticket = str(payload["client_ticket"])

    row = db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == license_id,
        TradeTicketMap.master_ticket == master_ticket,
        TradeTicketMap.client_ticket == client_ticket,
    ).first()

    if row:
        row.symbol = str(payload["symbol"]).strip()
        row.is_closed = bool(payload.get("is_closed", row.is_closed))
        row.closed_by_client = bool(payload.get("closed_by_client", row.closed_by_client))
        if payload.get("execution_id") is not None:
            row.execution_id = int(payload["execution_id"])
        if payload.get("closed_at"):
            row.closed_at = datetime.fromisoformat(str(payload["closed_at"]))
        db.commit()
        db.refresh(row)
        return {"message": "Ticket map updated", "id": row.id}

    new_row = TradeTicketMap(
        license_id=license_id,
        execution_id=payload.get("execution_id"),
        master_ticket=master_ticket,
        client_ticket=client_ticket,
        symbol=str(payload["symbol"]).strip(),
        is_closed=bool(payload.get("is_closed", False)),
        closed_by_client=bool(payload.get("closed_by_client", False)),
    )
    db.add(new_row)
    db.commit()
    db.refresh(new_row)

    return {"message": "Ticket map created", "id": new_row.id}


@router.post("/ticket-maps/mark-closed")
def mark_ticket_map_closed(
    payload: Dict[str, Any],
    _: bool = Depends(require_worker_token),
    db: Session = Depends(get_db),
):
    for field in ["license_id", "master_ticket"]:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")

    rows = db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == int(payload["license_id"]),
        TradeTicketMap.master_ticket == str(payload["master_ticket"]).strip(),
        TradeTicketMap.is_closed == False,  # noqa: E712
    ).all()

    if not rows:
        raise HTTPException(status_code=404, detail="Ticket map not found")

    now = utc_now()

    for row in rows:
        row.is_closed = True
        row.closed_by_client = bool(payload.get("closed_by_client", False))
        row.closed_at = now

    db.commit()

    return {"message": "Ticket maps closed successfully", "count": len(rows)}


@router.get("/ticket-maps/by-keys/all-open")
def get_open_ticket_maps_by_keys(
    license_id: int,
    ea_id: int,
    master_ticket: str,
    db: Session = Depends(get_db),
):
    rows = db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == license_id,
        TradeTicketMap.master_ticket == master_ticket,
        TradeTicketMap.is_closed == False,  # noqa: E712
    ).order_by(TradeTicketMap.id.asc()).all()

    return [serialize_ticket_map(row) for row in rows]
