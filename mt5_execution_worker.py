import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from app.services.metaapi_service import MetaApiService

BACKEND_URL = "https://nolimitz-backend-yfne.onrender.com"
POLL_SECONDS = 5
CLAIM_LIMIT = 10
MAX_OPEN_EVENT_AGE_SECONDS = 60


# =========================
# TIME HELPERS
# =========================
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_stale_open_execution(execution: dict) -> bool:
    if execution.get("event_type") != "open":
        return False

    created_at = execution.get("created_at")
    if not created_at:
        return False

    dt = parse_iso_datetime(created_at)
    age = (utc_now() - dt).total_seconds()
    return age > MAX_OPEN_EVENT_AGE_SECONDS


# =========================
# BACKEND HELPERS
# =========================
def backend_claim_pending_executions():
    res = requests.post(
        f"{BACKEND_URL}/copier/executions/claim",
        params={"limit": CLAIM_LIMIT},
        timeout=30,
    )
    res.raise_for_status()
    return res.json()


def backend_get_execution_account(execution_id: int):
    res = requests.get(
        f"{BACKEND_URL}/copier/executions/{execution_id}/account",
        timeout=30,
    )
    res.raise_for_status()
    return res.json()


def backend_update_execution(
    execution_id: int,
    status: str,
    client_ticket: Optional[str] = None,
    error_message: Optional[str] = None,
):
    payload = {
        "status": status,
        "client_ticket": str(client_ticket) if client_ticket is not None else None,
        "error_message": error_message,
    }
    res = requests.post(
        f"{BACKEND_URL}/copier/executions/{execution_id}/update",
        json=payload,
        timeout=30,
    )
    res.raise_for_status()
    return res.json()


def backend_get_client_symbol_settings(license_key: str):
    payload = {"license_key": license_key}
    res = requests.post(
        f"{BACKEND_URL}/client/symbols/list",
        json=payload,
        timeout=30,
    )
    res.raise_for_status()
    return res.json()


def backend_get_ticket_maps_by_keys(license_id: int, ea_id: int, master_ticket: str):
    res = requests.get(
        f"{BACKEND_URL}/copier/ticket-maps/by-keys",
        params={
            "license_id": license_id,
            "ea_id": ea_id,
            "master_ticket": master_ticket,
        },
        timeout=30,
    )

    if res.status_code == 404:
        return []

    res.raise_for_status()
    return res.json()


def backend_get_open_ticket_maps_by_keys(license_id: int, ea_id: int, master_ticket: str):
    res = requests.get(
        f"{BACKEND_URL}/copier/ticket-maps/by-keys/all-open",
        params={
            "license_id": license_id,
            "ea_id": ea_id,
            "master_ticket": master_ticket,
        },
        timeout=30,
    )
    res.raise_for_status()
    return res.json()


def backend_upsert_ticket_map(
    execution: dict,
    client_ticket: str,
    is_open: bool = True,
    manually_closed: bool = False,
    child_ticket_index: int = 1,
):
    payload = {
        "license_id": execution["license_id"],
        "ea_id": execution["ea_id"],
        "master_ticket": execution["master_ticket"],
        "client_ticket": str(client_ticket),
        "child_ticket_index": child_ticket_index,
        "symbol": execution["symbol"],
        "action": execution.get("action"),
        "is_open": is_open,
        "manually_closed": manually_closed,
    }

    res = requests.post(
        f"{BACKEND_URL}/copier/ticket-maps/upsert",
        json=payload,
        timeout=30,
    )
    res.raise_for_status()
    return res.json()


def backend_mark_ticket_map_closed(
    execution: dict,
    manually_closed: bool = False,
):
    payload = {
        "license_id": execution["license_id"],
        "ea_id": execution["ea_id"],
        "master_ticket": execution["master_ticket"],
        "manually_closed": manually_closed,
    }
    res = requests.post(
        f"{BACKEND_URL}/copier/ticket-maps/mark-closed",
        json=payload,
        timeout=30,
    )
    res.raise_for_status()
    return res.json()


# =========================
# METAAPI HELPERS
# =========================
def run_async(coro):
    return asyncio.run(coro)


def get_metaapi_service() -> MetaApiService:
    return MetaApiService()


def require_metaapi_account_id(account: dict) -> str:
    account_id = account.get("metaapi_account_id")
    if not account_id:
        raise Exception("No MetaApi account linked to this execution account")
    return str(account_id)


def count_open_positions_for_symbol(account: dict, broker_symbol: str) -> int:
    account_id = require_metaapi_account_id(account)
    positions = run_async(get_metaapi_service().get_positions(account_id))
    if not positions:
        return 0

    count = 0
    for pos in positions:
        pos_symbol = str(pos.get("symbol", "")).upper().strip()
        if pos_symbol == broker_symbol.upper().strip():
            count += 1
    return count


def mapped_ticket_still_open(account: dict, client_ticket: str) -> bool:
    account_id = require_metaapi_account_id(account)
    position = run_async(get_metaapi_service().get_position(account_id, str(client_ticket)))
    return bool(position)


def get_open_mapped_tickets_for_execution(account: dict, execution: dict) -> list[dict]:
    mappings = backend_get_open_ticket_maps_by_keys(
        license_id=execution["license_id"],
        ea_id=execution["ea_id"],
        master_ticket=execution["master_ticket"],
    )

    if not mappings:
        return []

    alive_mappings: list[dict] = []
    account_id = require_metaapi_account_id(account)

    for mapping in mappings:
        client_ticket = mapping.get("client_ticket")
        if not client_ticket:
            continue

        position = run_async(get_metaapi_service().get_position(account_id, str(client_ticket)))
        if position:
            alive_mappings.append(mapping)

    return alive_mappings


def mark_execution_as_manual_close_if_needed(account: dict, execution: dict) -> bool:
    existing_maps = backend_get_ticket_maps_by_keys(
        license_id=execution["license_id"],
        ea_id=execution["ea_id"],
        master_ticket=execution["master_ticket"],
    )

    if not existing_maps:
        return False

    manually_closed_found = any(m.get("manually_closed") is True for m in existing_maps)
    if manually_closed_found:
        return True

    open_maps = [m for m in existing_maps if m.get("is_open") is True and m.get("client_ticket")]
    if not open_maps:
        return False

    alive_maps = get_open_mapped_tickets_for_execution(account, execution)

    if len(open_maps) > 0 and len(alive_maps) == 0:
        backend_mark_ticket_map_closed(execution, manually_closed=True)
        print(
            f"[INFO] execution {execution['id']}: client manually closed mapped trade(s), "
            f"marking master_ticket={execution['master_ticket']} as manually_closed"
        )
        return True

    return False


def get_symbol_setting_for_execution(account: dict, execution: dict):
    requested_symbol = execution["symbol"].upper().strip()
    settings = backend_get_client_symbol_settings(account["license_key"])

    for item in settings:
        if item["symbol_name"].upper().strip() == requested_symbol:
            return item

    return None


def build_trade_comment(execution: dict) -> str:
    raw_comment = str(execution.get("comment") or "").strip()
    if raw_comment:
        return raw_comment[:30]
    return "Nolimitz Copier"


def normalize_optional_price(value):
    if value in [None, "", "0", 0]:
        return None
    return float(value)


# =========================
# TRADE EXECUTION
# =========================
def execute_single_open_trade(execution: dict, account: dict) -> str:
    account_id = require_metaapi_account_id(account)

    requested_symbol = execution["symbol"]
    symbol = run_async(get_metaapi_service().find_broker_symbol(account_id, requested_symbol))

    symbol_setting = get_symbol_setting_for_execution(account, execution)
    if not symbol_setting:
        raise Exception(f"No client symbol setting found for {requested_symbol}")

    max_open_trades = int(symbol_setting.get("max_open_trades", 1))
    current_open_count = count_open_positions_for_symbol(account, symbol)

    print(
        f"Symbol setting: requested={requested_symbol}, broker={symbol}, "
        f"current_open={current_open_count}, max_open={max_open_trades}"
    )

    if current_open_count >= max_open_trades:
        raise Exception(
            f"Max open trades reached for {requested_symbol}: "
            f"{current_open_count}/{max_open_trades}"
        )

    action = str(execution["action"]).lower().strip()
    lot_size = float(execution["lot_size"] or "0.01")

    sl = normalize_optional_price(execution.get("sl"))
    tp = normalize_optional_price(execution.get("tp"))
    comment = build_trade_comment(execution)

    client_id = f"NL_{execution['id']}_{int(time.time())}"

    if action == "buy":
        result = run_async(
            get_metaapi_service().create_market_buy_order(
                account_id=account_id,
                symbol=symbol,
                volume=lot_size,
                stop_loss=sl,
                take_profit=tp,
                comment=comment,
                client_id=client_id,
            )
        )
    else:
        result = run_async(
            get_metaapi_service().create_market_sell_order(
                account_id=account_id,
                symbol=symbol,
                volume=lot_size,
                stop_loss=sl,
                take_profit=tp,
                comment=comment,
                client_id=client_id,
            )
        )

    position_id = result.get("positionId") or result.get("orderId") or result.get("stringCode")
    if not position_id:
        raise Exception(f"MetaApi open order did not return positionId/orderId: {result}")

    return str(position_id)


def execute_open_trade(execution: dict, account: dict) -> list[str]:
    account_id = require_metaapi_account_id(account)

    requested_symbol = execution["symbol"]
    symbol = run_async(get_metaapi_service().find_broker_symbol(account_id, requested_symbol))

    symbol_setting = get_symbol_setting_for_execution(account, execution)
    if not symbol_setting:
        raise Exception(f"No client symbol setting found for {requested_symbol}")

    if not symbol_setting.get("enabled", True):
        raise Exception(f"Symbol is disabled for client: {requested_symbol}")

    max_open_trades = int(symbol_setting.get("max_open_trades", 1))
    trades_per_signal = int(symbol_setting.get("trades_per_signal", 1))
    current_open_count = count_open_positions_for_symbol(account, symbol)

    print(
        f"Symbol setting: requested={requested_symbol}, broker={symbol}, "
        f"current_open={current_open_count}, max_open={max_open_trades}, "
        f"trades_per_signal={trades_per_signal}"
    )

    available_slots = max_open_trades - current_open_count
    if available_slots <= 0:
        raise Exception(
            f"Max open trades reached for {requested_symbol}: "
            f"{current_open_count}/{max_open_trades}"
        )

    actual_trades_to_open = min(trades_per_signal, available_slots)
    tickets: list[str] = []

    for _ in range(actual_trades_to_open):
        ticket = execute_single_open_trade(execution, account)
        tickets.append(str(ticket))

    return tickets


def execute_modify_trade(execution: dict, account: dict) -> list[str]:
    account_id = require_metaapi_account_id(account)
    modified_tickets: list[str] = []

    mappings = backend_get_open_ticket_maps_by_keys(
        license_id=execution["license_id"],
        ea_id=execution["ea_id"],
        master_ticket=execution["master_ticket"],
    )

    if not mappings:
        raise Exception("No open ticket maps found for this master ticket")

    sl = normalize_optional_price(execution.get("sl"))
    tp = normalize_optional_price(execution.get("tp"))

    for mapping in mappings:
        client_ticket = str(mapping["client_ticket"])
        position = run_async(get_metaapi_service().get_position(account_id, client_ticket))
        if not position:
            print(f"[SKIP MODIFY] client trade {client_ticket} not found")
            continue

        try:
            run_async(
                get_metaapi_service().modify_position(
                    account_id=account_id,
                    position_id=client_ticket,
                    stop_loss=sl,
                    take_profit=tp,
                )
            )
            modified_tickets.append(client_ticket)
        except Exception as e:
            print(f"[FAIL MODIFY] {client_ticket}: {e}")

    return modified_tickets


def execute_close_trade(execution: dict, account: dict) -> list[str]:
    account_id = require_metaapi_account_id(account)
    closed_tickets: list[str] = []

    mappings = backend_get_open_ticket_maps_by_keys(
        license_id=execution["license_id"],
        ea_id=execution["ea_id"],
        master_ticket=execution["master_ticket"],
    )

    if not mappings:
        raise Exception("No open ticket maps found for this master ticket")

    for mapping in mappings:
        client_ticket = str(mapping["client_ticket"])
        position = run_async(get_metaapi_service().get_position(account_id, client_ticket))
        if not position:
            print(f"[SKIP CLOSE] client trade {client_ticket} already closed manually")
            continue

        try:
            run_async(
                get_metaapi_service().close_position(
                    account_id=account_id,
                    position_id=client_ticket,
                )
            )
            closed_tickets.append(client_ticket)
        except Exception as e:
            print(f"[FAIL CLOSE] {client_ticket}: {e}")

    if closed_tickets:
        backend_mark_ticket_map_closed(execution, manually_closed=False)
    else:
        alive_maps = get_open_mapped_tickets_for_execution(account, execution)
        if not alive_maps:
            backend_mark_ticket_map_closed(execution, manually_closed=True)
            print(
                f"[INFO] close execution {execution['id']}: "
                f"all mapped tickets already missing, marked manually_closed=True"
            )

    return closed_tickets


# =========================
# CORE PROCESSOR
# =========================
def process_execution(execution: dict):
    execution_id = execution["id"]

    try:
        account = backend_get_execution_account(execution_id)

        if execution["event_type"] == "open":
            if is_stale_open_execution(execution):
                backend_update_execution(
                    execution_id=execution_id,
                    status="skipped",
                    client_ticket=None,
                    error_message="Skipped old open event",
                )
                print(f"[SKIP] execution {execution_id}: old open event")
                return

            if mark_execution_as_manual_close_if_needed(account, execution):
                backend_update_execution(
                    execution_id=execution_id,
                    status="skipped",
                    client_ticket=None,
                    error_message="Trade was manually closed by client; will not reopen",
                )
                print(f"[SKIP] execution {execution_id}: manually closed trade will not reopen")
                return

            existing_maps = backend_get_ticket_maps_by_keys(
                license_id=execution["license_id"],
                ea_id=execution["ea_id"],
                master_ticket=execution["master_ticket"],
            )

            if existing_maps:
                open_map_found = False

                for existing_map in existing_maps:
                    mapped_client_ticket = existing_map.get("client_ticket")
                    if existing_map.get("is_open") is True and mapped_client_ticket:
                        if mapped_ticket_still_open(account, mapped_client_ticket):
                            open_map_found = True
                            break

                if open_map_found:
                    backend_update_execution(
                        execution_id=execution_id,
                        status="skipped",
                        client_ticket=None,
                        error_message="Trade already exists for this master ticket",
                    )
                    print(f"[SKIP] execution {execution_id}: duplicate open prevented")
                    return

                try:
                    backend_mark_ticket_map_closed(execution, manually_closed=False)
                    print(
                        f"[INFO] execution {execution_id}: stale open maps detected, "
                        f"maps closed and continuing"
                    )
                except Exception as e:
                    print(f"[WARN] execution {execution_id}: could not close stale maps: {e}")

            tickets = execute_open_trade(execution, account)
            first_ticket = tickets[0] if tickets else None

            for idx, ticket in enumerate(tickets, start=1):
                backend_upsert_ticket_map(
                    execution=execution,
                    client_ticket=ticket,
                    is_open=True,
                    manually_closed=False,
                    child_ticket_index=idx,
                )

            backend_update_execution(
                execution_id=execution_id,
                status="executed" if tickets else "skipped",
                client_ticket=first_ticket,
                error_message=None if tickets else "No child trades were opened",
            )
            print(f"[OK] execution {execution_id} opened {len(tickets)} trade(s): {tickets}")
            return

        if execution["event_type"] == "modify":
            modified_tickets = execute_modify_trade(execution, account)
            backend_update_execution(
                execution_id=execution_id,
                status="executed" if modified_tickets else "skipped",
                client_ticket=",".join(modified_tickets) if modified_tickets else None,
                error_message=None if modified_tickets else "No child trades were modified",
            )
            print(f"[OK] execution {execution_id} modified tickets: {modified_tickets}")
            return

        if execution["event_type"] == "close":
            closed_tickets = execute_close_trade(execution, account)
            backend_update_execution(
                execution_id=execution_id,
                status="executed" if closed_tickets else "skipped",
                client_ticket=",".join(closed_tickets) if closed_tickets else None,
                error_message=None if closed_tickets else "No child trades were closed",
            )
            print(f"[OK] execution {execution_id} closed tickets: {closed_tickets}")
            return

        backend_update_execution(
            execution_id=execution_id,
            status="failed",
            client_ticket=None,
            error_message=f"Unknown event type: {execution['event_type']}",
        )

    except Exception as e:
        message = str(e)

        status = "failed"
        if "Max open trades reached" in message:
            status = "skipped"
        if "Skipped old open event" in message:
            status = "skipped"
        if "No client symbol setting found" in message:
            status = "skipped"
        if "Trade already exists for this master ticket" in message:
            status = "skipped"
        if "Trade was manually closed by client; will not reopen" in message:
            status = "skipped"
        if "Symbol is disabled for client" in message:
            status = "skipped"

        backend_update_execution(
            execution_id=execution_id,
            status=status,
            client_ticket=None,
            error_message=message,
        )

        label = "SKIP" if status == "skipped" else "FAIL"
        print(f"[{label}] execution {execution_id}: {message}")


# =========================
# LOOP
# =========================
def main():
    print("Nolimitz MetaApi Execution Worker started...")
    while True:
        try:
            executions = backend_claim_pending_executions()
            if executions:
                print(f"Found {len(executions)} pending executions")
                for execution in executions:
                    process_execution(execution)
            time.sleep(POLL_SECONDS)
        except Exception as e:
            print(f"[WORKER ERROR] {e}")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()