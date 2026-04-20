from dotenv import load_dotenv
load_dotenv(dotenv_path=".env.local", override=True)

import os
import time
import requests
import MetaTrader5 as mt5

BACKEND_URL = os.getenv("BACKEND_URL", "https://nolimitz-backend-yfne.onrender.com")
MASTER_EA_ID = int(os.getenv("MASTER_EA_ID", "1"))

MASTER_MT5_LOGIN = os.getenv("MASTER_MT5_LOGIN")
MASTER_MT5_PASSWORD = os.getenv("MASTER_MT5_PASSWORD")
MASTER_MT5_SERVER = os.getenv("MASTER_MT5_SERVER")
MASTER_API_TOKEN = os.getenv("MASTER_API_TOKEN")

MT5_PATH = r"C:\Users\user\Desktop\NolimitzMT5Verifier\terminal64.exe"

if not MASTER_MT5_LOGIN or not MASTER_MT5_PASSWORD or not MASTER_MT5_SERVER:
    raise RuntimeError(
        "MASTER_MT5_LOGIN / MASTER_MT5_PASSWORD / MASTER_MT5_SERVER must be set in .env.local"
    )

if not MASTER_API_TOKEN:
    raise RuntimeError("MASTER_API_TOKEN must be set in .env.local")

seen_positions: dict[str, dict] = {}


def normalize_action(position_type: int) -> str:
    if position_type == mt5.POSITION_TYPE_BUY:
        return "buy"
    return "sell"


def safe_number(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {MASTER_API_TOKEN}",
        "Content-Type": "application/json",
    }


def send_open_trade(position) -> None:
    payload = {
        "ea_id": MASTER_EA_ID,
        "master_ticket": str(position.ticket),
        "symbol": str(position.symbol),
        "action": normalize_action(position.type),
        "lot_size": str(safe_number(position.volume, 0.01)),
        "sl": str(safe_number(position.sl, 0.0)),
        "tp": str(safe_number(position.tp, 0.0)),
        "price": str(safe_number(position.price_open, 0.0)),
        "comment": position.comment or "Master trade",
    }

    try:
        response = requests.post(
            f"{BACKEND_URL}/copier/open",
            json=payload,
            headers=auth_headers(),
            timeout=20,
        )
        print(f"OPEN SENT [{response.status_code}] ticket={position.ticket} symbol={position.symbol}")
        print(response.text)
    except Exception as e:
        print(f"OPEN SEND FAILED ticket={position.ticket}: {e}")


def send_close_trade(ticket: str, position_data: dict) -> None:
    payload = {
        "ea_id": MASTER_EA_ID,
        "master_ticket": str(ticket),
        "symbol": str(position_data["symbol"]),
        "action": str(position_data["action"]),
        "comment": "Master trade closed",
    }

    try:
        response = requests.post(
            f"{BACKEND_URL}/copier/close",
            json=payload,
            headers=auth_headers(),
            timeout=20,
        )
        print(f"CLOSE SENT [{response.status_code}] ticket={ticket} symbol={position_data['symbol']}")
        print(response.text)
    except Exception as e:
        print(f"CLOSE SEND FAILED ticket={ticket}: {e}")


def main():
    if not os.path.exists(MT5_PATH):
        raise RuntimeError(f"MT5 terminal not found at path: {MT5_PATH}")

    if not mt5.initialize(path=MT5_PATH):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    login_ok = mt5.login(
        int(MASTER_MT5_LOGIN),
        password=MASTER_MT5_PASSWORD,
        server=MASTER_MT5_SERVER,
    )
    if not login_ok:
        raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")

    print("Master MT5 bridge started...")

    try:
        while True:
            positions = mt5.positions_get()

            if positions is None:
                print(f"positions_get returned None: {mt5.last_error()}")
                time.sleep(2)
                continue

            current_positions: dict[str, dict] = {}

            for pos in positions:
                ticket = str(pos.ticket)
                current_positions[ticket] = {
                    "symbol": str(pos.symbol),
                    "action": normalize_action(pos.type),
                    "volume": safe_number(pos.volume, 0.01),
                    "sl": safe_number(pos.sl, 0.0),
                    "tp": safe_number(pos.tp, 0.0),
                    "price_open": safe_number(pos.price_open, 0.0),
                    "comment": pos.comment or "Master trade",
                }

                if ticket not in seen_positions:
                    print(f"New master trade detected: ticket={ticket} symbol={pos.symbol}")
                    send_open_trade(pos)

            closed_tickets = set(seen_positions.keys()) - set(current_positions.keys())

            for ticket in closed_tickets:
                old_pos = seen_positions[ticket]
                print(f"Master trade closed detected: ticket={ticket} symbol={old_pos['symbol']}")
                send_close_trade(ticket, old_pos)

            seen_positions.clear()
            seen_positions.update(current_positions)

            time.sleep(2)

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()