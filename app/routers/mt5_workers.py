"""
================================================================================
  COPIER ROUTER  —  master account → licensed client accounts
================================================================================

  ── FIXES IN THIS REVISION ───────────────────────────────────────────────────
  1. `TradeTicketMap.is_closed` → `is_open`. The close branch referenced a
     column that does not exist, so EVERY /copier/close 500'd with an
     AttributeError while opens worked. Closes would never have propagated.
  2. Removed `import asyncio` and `from app.execution_dispatcher import
     dispatch_trade`. The dispatch call itself was already deleted (it was the
     `no running event loop` crash); these leftovers still dragged
     MetaApiService into every request through the import chain.
  3. `/copier/executions/{id}/account` referenced `mt5.mt_login` / `mt5.mt_server`,
     which are not columns on ClientMT5Account (they are `login` / `server`), so
     it 500'd whenever it got that far. Fixed, and the hard MetaAPI requirement
     downgraded to an optional field since MetaAPI was dropped.
  4. Machine-only routes (claim / update / ticket-map writes) now require the
     same X-Worker-Token used by /worker/*. Nothing calls them over HTTP any
     more — the execution worker reads and writes these rows directly through
     the database — so this closes the hole without breaking a caller.

  ── HOW EXECUTION ACTUALLY HAPPENS ───────────────────────────────────────────
  This router only CREATES pending TradeExecution rows. It does not dispatch.
  `app/ai/copier_executor.py`, running inside ai_execution_worker, polls for
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
# cross-router import was the only structural change in the previous revision,
# and the deploy hung before binding a port — so every router stays
# self-contained from here on.
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


def serialize_ticket_map(row: TradeTicketMap) -> TradeTicketMapItem:
    return TradeTicketMapItem(
        id=row.id,
        license_id=row.license_id,
        ea_id=row.ea_id,
        master_ticket=row.master_ticket,
        child_ticket_index=row.child_ticket_index,
        client_ticket=row.client_ticket,
        symbol=row.symbol,
        action=row.action,
        is_open=row.is_open,
        manually_closed=row.manually_closed,
        opened_at=row.opened_at,
        closed_at=row.closed_at,
    )


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def get_symbol_aliases(symbol: str) -> list[str]:
    """Brokers name the same instrument differently — XAUUSD, XAUUSDm, GOLD.
    The client enabled whichever their broker offers, so match them all.

    NOTE: app/ai/copier_executor.py must use the SAME alias list when it looks
    up the client's symbol setting, or the router will create rows the executor
    then skips as "not enabled by client".
    """
    base = normalize_symbol(symbol)

    alias_map = {
        "XAUUSD": ["XAUUSD", "XAUUSDM", "XAUUSDC", "GOLD", "GOLDM", "XAUUSD."],
        "BTCUSD": ["BTCUSD", "BTCUSDM", "BTCUSDT", "BTCUSD."],
        "ETHUSD": ["ETHUSD", "ETHUSDM", "ETHUSDT", "ETHUSD."],
        "EURUSD": ["EURUSD", "EURUSDM", "EURUSDC", "EURUSD."],
        "GBPUSD": ["GBPUSD", "GBPUSDM", "GBPUSDC", "GBPUSD."],
        "USDJPY": ["USDJPY", "USDJPYM", "USDJPYC", "USDJPY."],
    }

    return [normalize_symbol(x) for x in alias_map.get(base, [base])]


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
    normalized_symbol = normalize_symbol(event.symbol)
    symbol_aliases = get_symbol_aliases(normalized_symbol)

    for license_row in licenses:

        mt5_account = db.query(ClientMT5Account).filter(
            ClientMT5Account.license_id == license_row.id,
            ClientMT5Account.is_active == True,
            ClientMT5Account.is_verified == True,
        ).first()

        if not mt5_account:
            print(f"[SKIP] license={license_row.id} reason=no_active_verified_mt5")
            continue

        symbol_setting = db.query(ClientSymbolSetting).filter(
            ClientSymbolSetting.license_id == license_row.id,
            ClientSymbolSetting.symbol_name.in_(symbol_aliases),
            ClientSymbolSetting.enabled == True,
        ).first()

        if not symbol_setting:
            print(f"[SKIP] license={license_row.id} reason=symbol_not_enabled")
            continue

        # Direction preference applies to OPEN only — a client who only takes
        # buys must still be able to close and modify what they already hold.
        if event.event_type == "open":
            client_direction = (symbol_setting.trade_direction or "both").lower()
            event_action = (event.action or "").lower()

            if client_direction != "both" and client_direction != event_action:
                print(f"[SKIP] license={license_row.id} reason=direction_blocked")
                continue

        # For CLOSE, only queue clients that actually hold an open copy of this
        # master ticket. The column is is_open — is_closed does not exist and
        # referencing it 500'd every close event.
        if event.event_type == "close":
            open_map = db.query(TradeTicketMap).filter(
                TradeTicketMap.license_id == license_row.id,
                TradeTicketMap.master_ticket == event.master_ticket,
                TradeTicketMap.is_open == True,
            ).first()

            if not open_map:
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

        print(f"[EXEC CREATED] license={license_row.id}")

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
        comment=comment,
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
    the database inside the execution worker. Left behind a worker token so an
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
@router.get("/ticket-maps", response_model=List[TradeTicketMapItem])
def list_ticket_maps(
    db: Session = Depends(get_db),
):
    rows = db.query(TradeTicketMap).order_by(TradeTicketMap.id.desc()).all()
    return [serialize_ticket_map(row) for row in rows]


@router.get("/ticket-maps/by-execution/{execution_id}", response_model=List[TradeTicketMapItem])
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
        TradeTicketMap.ea_id == execution.ea_id,
        TradeTicketMap.master_ticket == execution.master_ticket,
    ).order_by(TradeTicketMap.child_ticket_index.asc()).all()

    if not rows:
        raise HTTPException(status_code=404, detail="Ticket map not found")

    return [serialize_ticket_map(row) for row in rows]


@router.get("/ticket-maps/by-keys", response_model=List[TradeTicketMapItem])
def get_ticket_maps_by_keys(
    license_id: int,
    ea_id: int,
    master_ticket: str,
    db: Session = Depends(get_db),
):
    rows = db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == license_id,
        TradeTicketMap.ea_id == ea_id,
        TradeTicketMap.master_ticket == master_ticket,
    ).order_by(TradeTicketMap.child_ticket_index.asc()).all()

    if not rows:
        raise HTTPException(status_code=404, detail="Ticket map not found")

    return [serialize_ticket_map(row) for row in rows]


@router.post("/ticket-maps/upsert")
def upsert_ticket_map(
    payload: Dict[str, Any],
    _: bool = Depends(require_worker_token),
    db: Session = Depends(get_db),
):
    required_fields = ["license_id", "ea_id", "master_ticket", "client_ticket", "symbol"]
    for field in required_fields:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")

    license_id = int(payload["license_id"])
    ea_id = int(payload["ea_id"])
    master_ticket = str(payload["master_ticket"]).strip()
    child_ticket_index = int(payload.get("child_ticket_index", 1))

    row = db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == license_id,
        TradeTicketMap.ea_id == ea_id,
        TradeTicketMap.master_ticket == master_ticket,
        TradeTicketMap.child_ticket_index == child_ticket_index,
    ).first()

    if row:
        row.client_ticket = str(payload["client_ticket"])
        row.symbol = str(payload["symbol"]).strip()
        row.action = payload.get("action")
        row.is_open = bool(payload.get("is_open", row.is_open))
        row.manually_closed = bool(payload.get("manually_closed", row.manually_closed))

        if payload.get("closed_at"):
            row.closed_at = datetime.fromisoformat(str(payload["closed_at"]))

        db.commit()
        db.refresh(row)
        return {"message": "Ticket map updated", "id": row.id}

    new_row = TradeTicketMap(
        license_id=license_id,
        ea_id=ea_id,
        master_ticket=master_ticket,
        child_ticket_index=child_ticket_index,
        client_ticket=str(payload["client_ticket"]),
        symbol=str(payload["symbol"]).strip(),
        action=payload.get("action"),
        is_open=bool(payload.get("is_open", True)),
        manually_closed=bool(payload.get("manually_closed", False)),
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
    required_fields = ["license_id", "ea_id", "master_ticket"]
    for field in required_fields:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")

    rows = db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == int(payload["license_id"]),
        TradeTicketMap.ea_id == int(payload["ea_id"]),
        TradeTicketMap.master_ticket == str(payload["master_ticket"]).strip(),
        TradeTicketMap.is_open == True,
    ).all()

    if not rows:
        raise HTTPException(status_code=404, detail="Ticket map not found")

    now = utc_now()

    for row in rows:
        row.is_open = False
        row.manually_closed = bool(payload.get("manually_closed", False))
        row.closed_at = now

    db.commit()

    return {"message": "Ticket maps closed successfully", "count": len(rows)}


@router.get("/ticket-maps/by-keys/all-open", response_model=List[TradeTicketMapItem])
def get_open_ticket_maps_by_keys(
    license_id: int,
    ea_id: int,
    master_ticket: str,
    db: Session = Depends(get_db),
):
    rows = db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == license_id,
        TradeTicketMap.ea_id == ea_id,
        TradeTicketMap.master_ticket == master_ticket,
        TradeTicketMap.is_open == True,
    ).order_by(TradeTicketMap.child_ticket_index.asc()).all()

    return [serialize_ticket_map(row) for row in rows]
