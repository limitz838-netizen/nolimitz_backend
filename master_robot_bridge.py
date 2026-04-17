import time
from typing import Any, Dict, Set

import MetaTrader5 as mt5
import requests

from bridge_config import (
    BACKEND_URL,
    ADMIN_TOKEN,
    MT5_TERMINAL_PATH,
    MASTER_LOGIN,
    MASTER_PASSWORD,
    MASTER_SERVER,
    EA_CODE,
    ALLOWED_MAGIC,
    ALLOWED_COMMENT_KEYWORD,
)

POLL_SECONDS = 2
REQUEST_TIMEOUT_SECONDS = 10


# =========================
# HELPERS
# =========================
def initialize_and_login() -> None:
    if not mt5.initialize(path=MT5_TERMINAL_PATH):
        raise Exception(f"MT5 initialize failed: {mt5.last_error()}")

    authorized = mt5.login(
        login=int(MASTER_LOGIN),
        password=MASTER_PASSWORD,
        server=MASTER_SERVER,
    )
    if not authorized:
        raise Exception(f"MT5 login failed: {mt5.last_error()}")


def shutdown_mt5() -> None:
    try:
        mt5.shutdown()
    except Exception:
        pass


def normalize_symbol(symbol: str) -> str:
    s = str(symbol).strip().upper()

    suffixes = ["M", ".M", "MICRO", "_M"]
    for suffix in suffixes:
        if s.endswith(suffix):
            return s[: -len(suffix)]

    return s


def is_allowed_master_trade(position: Any) -> bool:
    trade_magic = int(getattr(position, "magic", 0) or 0)
    trade_comment = str(getattr(position, "comment", "") or "").lower().strip()

    if trade_magic != ALLOWED_MAGIC:
        return False

    if ALLOWED_COMMENT_KEYWORD not in trade_comment:
        return False

    return True


def normalize_position(pos: Any) -> Dict[str, Any]:
    return {
        "ticket": str(pos.ticket),
        "symbol": str(pos.symbol),
        "type": "buy" if pos.type == mt5.POSITION_TYPE_BUY else "sell",
        "volume": float(pos.volume),
        "sl": float(pos.sl) if pos.sl is not None else 0.0,
        "tp": float(pos.tp) if pos.tp is not None else 0.0,
        "price_open": float(pos.price_open),
        "comment": str(pos.comment or ""),
        "magic": int(getattr(pos, "magic", 0) or 0),
    }


def get_open_positions_map(filtered_log_cache: Set[str]) -> Dict[str, Dict[str, Any]]:
    positions = mt5.positions_get()
    if positions is None:
        positions = []

    current_positions: Dict[str, Dict[str, Any]] = {}
    current_visible_tickets: Set[str] = set()

    for pos in positions:
        ticket = str(pos.ticket)
        current_visible_tickets.add(ticket)

        if not is_allowed_master_trade(pos):
            if ticket not in filtered_log_cache:
                print(
                    f"[FILTERED OUT] ticket={pos.ticket} symbol={pos.symbol} "
                    f"magic={getattr(pos, 'magic', 0)} comment={getattr(pos, 'comment', '')}"
                )
                filtered_log_cache.add(ticket)
            continue

        normalized = normalize_position(pos)
        current_positions[normalized["ticket"]] = normalized

    # remove closed/disappeared tickets from filtered log cache
    filtered_log_cache.intersection_update(current_visible_tickets)

    return current_positions


def auth_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {ADMIN_TOKEN}",
        "Content-Type": "application/json",
    }


def post_to_backend(path: str, payload: Dict[str, Any], label: str) -> None:
    try:
        res = requests.post(
            f"{BACKEND_URL}{path}",
            json=payload,
            headers=auth_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        print(f"[{label}]", res.status_code, res.text)
    except Exception as e:
        print(f"[ERROR {label}]", str(e))


def send_open(trade: Dict[str, Any]) -> None:
    payload = {
        "ea_code": EA_CODE,
        "master_ticket": trade["ticket"],
        "symbol": normalize_symbol(trade["symbol"]),
        "action": trade["type"].lower(),
        "sl": str(trade["sl"]),
        "tp": str(trade["tp"]),
        "price": str(trade["price_open"]),
        "comment": trade["comment"] or "robot",
    }
    post_to_backend("/copier/open", payload, "SENT TO COPIER OPEN")


def send_close(trade: Dict[str, Any]) -> None:
    payload = {
        "ea_code": EA_CODE,
        "master_ticket": trade["ticket"],
        "symbol": normalize_symbol(trade["symbol"]),
        "comment": trade["comment"] or "robot close",
    }
    post_to_backend("/copier/close", payload, "SENT TO COPIER CLOSE")


def send_modify(trade: Dict[str, Any]) -> None:
    payload = {
        "ea_code": EA_CODE,
        "master_ticket": trade["ticket"],
        "symbol": normalize_symbol(trade["symbol"]),
        "sl": str(trade["sl"]),
        "tp": str(trade["tp"]),
        "price": str(trade["price_open"]),
        "comment": trade["comment"] or "robot modify",
    }
    post_to_backend("/copier/modify", payload, "SENT TO COPIER MODIFY")


# =========================
# DETECTION LOOP
# =========================
def main() -> None:
    print("Starting Nolimitz master robot bridge...")
    print(f"EA_CODE={EA_CODE}")
    print(f"ALLOWED_MAGIC={ALLOWED_MAGIC}")
    print(f"ALLOWED_COMMENT_KEYWORD={ALLOWED_COMMENT_KEYWORD}")

    initialize_and_login()

    try:
        previous_positions: Dict[str, Dict[str, Any]] = {}
        filtered_log_cache: Set[str] = set()

        while True:
            try:
                current_positions = get_open_positions_map(filtered_log_cache)

                previous_tickets = set(previous_positions.keys())
                current_tickets = set(current_positions.keys())

                opened_tickets = current_tickets - previous_tickets
                closed_tickets = previous_tickets - current_tickets
                still_open_tickets = current_tickets & previous_tickets

                # NEW OPEN TRADES
                for ticket in opened_tickets:
                    send_open(current_positions[ticket])

                # CLOSED TRADES
                for ticket in closed_tickets:
                    send_close(previous_positions[ticket])

                # MODIFIED TRADES
                for ticket in still_open_tickets:
                    old_trade = previous_positions[ticket]
                    new_trade = current_positions[ticket]

                    sl_changed = old_trade["sl"] != new_trade["sl"]
                    tp_changed = old_trade["tp"] != new_trade["tp"]

                    if sl_changed or tp_changed:
                        send_modify(new_trade)

                previous_positions = current_positions
                time.sleep(POLL_SECONDS)

            except Exception as e:
                print("[ERROR INSIDE BRIDGE LOOP]", str(e))
                time.sleep(POLL_SECONDS)

    finally:
        shutdown_mt5()


if __name__ == "__main__":
    main()