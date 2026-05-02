from dotenv import load_dotenv
load_dotenv(dotenv_path=".env.local", override=True)

import os
import time
import requests
import MetaTrader5 as mt5
import socket
import threading
import logging
from datetime import datetime

# ========================= CONFIG =========================
BACKEND_URL = os.getenv("BACKEND_URL", "https://nolimitz-backend-yfne.onrender.com").rstrip("/")
MASTER_EA_ID = int(os.getenv("MASTER_EA_ID", "1"))

MASTER_MT5_LOGIN = os.getenv("MASTER_MT5_LOGIN")
MASTER_MT5_PASSWORD = os.getenv("MASTER_MT5_PASSWORD")
MASTER_MT5_SERVER = os.getenv("MASTER_MT5_SERVER")
MASTER_API_TOKEN = os.getenv("MASTER_API_TOKEN")

MT5_PATH = r"C:\Users\user\Desktop\NolimitzMT5Verifier\terminal64.exe"

POLL_INTERVAL = 1.5  # seconds

# ========================= HEARTBEAT CONFIG =========================
WORKER_NAME = os.getenv("WORKER_NAME", f"master-bridge-{socket.gethostname()}")   # This is what Lovable suggested
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "25"))  # slightly different from worker

# ========================= LOGGING =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ========================= HEARTBEAT SYSTEM =========================
def _register_bridge():
    try:
        payload = {
            "worker_name": WORKER_NAME,
            "worker_type": "master-bridge",
            "status": "online",
            "host": socket.gethostname(),
            "terminal_path": MT5_PATH,
        }

        logger.info(f"REGISTER PAYLOAD: {payload}")

        r = requests.post(
            f"{BACKEND_URL}/mt5-workers/register",
            json=payload,
            headers=auth_headers(),   # ✅ IMPORTANT
            timeout=10,
        )

        logger.info(f"REGISTER RESPONSE: {r.status_code} | {r.text}")

    except Exception as e:
        logger.error(f"[BRIDGE HEARTBEAT] Register error: {e}")


def _send_bridge_heartbeat():
    try:
        r = requests.post(
            f"{BACKEND_URL}/mt5-workers/{WORKER_NAME}/heartbeat",
            timeout=8,
        )
        if r.status_code == 404:
            _register_bridge()
    except Exception:
        pass  # silent


def start_bridge_heartbeat():
    _register_bridge()
    def heartbeat_loop():
        while True:
            _send_bridge_heartbeat()
            time.sleep(HEARTBEAT_INTERVAL)
    threading.Thread(target=heartbeat_loop, daemon=True, name="BridgeHeartbeat").start()
    logger.info(f"[BRIDGE HEARTBEAT] Started for {WORKER_NAME} (every {HEARTBEAT_INTERVAL}s)")


# ========================= HELPERS =========================
def normalize_action(position_type: int) -> str:
    return "buy" if position_type == mt5.POSITION_TYPE_BUY else "sell"


def safe_number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {MASTER_API_TOKEN}",
        "Content-Type": "application/json",
    }


def send_open_trade(position) -> bool:
    try:
        payload = {
            "ea_id": MASTER_EA_ID,
            "master_ticket": str(position.ticket),
            "symbol": str(position.symbol),
            "action": normalize_action(position.type),
            "lot_size": str(safe_number(position.volume, 0.01)),
            "sl": str(safe_number(position.sl)),
            "tp": str(safe_number(position.tp)),
            "price": str(safe_number(position.price_open)),
            "comment": position.comment or "Master trade",
        }

        logger.info(f"SENDING EA ID: {MASTER_EA_ID}")
        logger.info(f"SENDING PAYLOAD: {payload}")

        response = requests.post(
            f"{BACKEND_URL}/copier/open",
            json=payload,
            headers=auth_headers(),
            timeout=15,
        )

        logger.info(f"BACKEND RESPONSE: {response.status_code} | {response.text}")

        if response.status_code in (200, 201):
            logger.info(f"OPEN SENT ✓ ticket={position.ticket}")
            return True
        else:
            logger.error(f"OPEN FAILED [{response.status_code}] ticket={position.ticket}")
            return False

    except Exception as e:
        logger.error(f"OPEN SEND ERROR ticket={position.ticket}: {e}")
        return False


def send_close_trade(ticket: str, position_data: dict) -> bool:
    payload = {
        "ea_id": MASTER_EA_ID,
        "master_ticket": str(ticket),
        "symbol": position_data["symbol"],
        "action": position_data["action"],
        "comment": "Master trade closed",
    }

    try:
        response = requests.post(
            f"{BACKEND_URL}/copier/close",
            json=payload,
            headers=auth_headers(),
            timeout=15,
        )

        logger.info(f"CLOSE RESPONSE: {response.status_code} | {response.text}")

        if response.status_code in (200, 201):
            logger.info(f"CLOSE SENT ✓ ticket={ticket} | {position_data['symbol']}")
            return True
        else:
            logger.warning(f"CLOSE FAILED [{response.status_code}] ticket={ticket}")
            return False
    except Exception as e:
        logger.error(f"CLOSE SEND ERROR ticket={ticket}: {e}")
        return False


# ========================= MAIN =========================
def main():
    if not all([MASTER_MT5_LOGIN, MASTER_MT5_PASSWORD, MASTER_MT5_SERVER, MASTER_API_TOKEN]):
        raise RuntimeError("Missing MASTER_ environment variables")

    if not os.path.exists(MT5_PATH):
        raise RuntimeError(f"MT5 terminal not found at: {MT5_PATH}")

    if not mt5.initialize(path=MT5_PATH):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    if not mt5.login(int(MASTER_MT5_LOGIN), password=MASTER_MT5_PASSWORD, server=MASTER_MT5_SERVER):
        raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")

    logger.info("Master MT5 Bridge started successfully")
    start_bridge_heartbeat()        # ← Start heartbeat

    seen_positions = {}

    try:
        while True:
            try:
                positions = mt5.positions_get()
                if positions is None:
                    logger.error(f"positions_get failed: {mt5.last_error()}")
                    time.sleep(POLL_INTERVAL)
                    continue

                current_positions = {}
                new_positions = 0

                for pos in positions:
                    ticket = str(pos.ticket)
                    pos_dict = {
                        "symbol": str(pos.symbol),
                        "action": normalize_action(pos.type),
                        "volume": safe_number(pos.volume),
                    }
                    current_positions[ticket] = pos_dict

                    if ticket not in seen_positions:
                        logger.info(f"New master trade detected: {ticket} | {pos.symbol}")
                        if send_open_trade(pos):
                            new_positions += 1

                # Detect closed trades
                closed_tickets = set(seen_positions.keys()) - set(current_positions.keys())
                for ticket in closed_tickets:
                    old_data = seen_positions[ticket]
                    logger.info(f"Master trade closed: {ticket} | {old_data['symbol']}")
                    send_close_trade(ticket, old_data)

                seen_positions = current_positions.copy()

                if new_positions > 0 or closed_tickets:
                    logger.info(f"Cycle: {new_positions} new opens, {len(closed_tickets)} closes")

            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Master Bridge stopped by user")
    finally:
        mt5.shutdown()
        logger.info("MT5 shutdown completed")


if __name__ == "__main__":
    main()