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
from typing import Dict, Optional

session = requests.Session()

# ========================= CONFIG =========================
BACKEND_URL = os.getenv("BACKEND_URL", "https://nolimitz-backend-yfne.onrender.com").rstrip("/")
MASTER_EA_ID = int(os.getenv("MASTER_EA_ID", "1"))

MASTER_MT5_LOGIN = os.getenv("MASTER_MT5_LOGIN")
MASTER_MT5_PASSWORD = os.getenv("MASTER_MT5_PASSWORD")
MASTER_MT5_SERVER = os.getenv("MASTER_MT5_SERVER")
MASTER_API_TOKEN = os.getenv("MASTER_API_TOKEN")

MT5_PATH = r"C:\Users\user\Desktop\NolimitzMT5Verifier\terminal64.exe"

POLL_INTERVAL = 1.5
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "25"))

# ========================= LOGGING =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ========================= AUTH =========================
def auth_headers() -> dict:
    if not MASTER_API_TOKEN:
        raise RuntimeError("MASTER_API_TOKEN is not set")
    return {
        "Authorization": f"Bearer {MASTER_API_TOKEN}",
        "Content-Type": "application/json",
    }


# ========================= HEARTBEAT =========================
def register_bridge():
    try:
        payload = {
            "worker_name": WORKER_NAME,
            "worker_type": "master-bridge",
            "status": "online",
            "host": socket.gethostname(),
            "terminal_path": MT5_PATH,
        }
        r = session.post(
            f"{BACKEND_URL}/mt5-workers/register",
            json=payload,
            headers=auth_headers(),
            timeout=10,
        )
        logger.info(f"Bridge registered → {r.status_code}")
    except Exception as e:
        logger.error(f"Bridge registration failed: {e}")


def send_heartbeat():
    try:
        r = session.post(
            f"{BACKEND_URL}/mt5-workers/{WORKER_NAME}/heartbeat",
            headers=auth_headers(),
            timeout=8,
        )
        if r.status_code == 404:
            register_bridge()
    except Exception:
        pass  # Silent failure


def start_heartbeat():
    register_bridge()
    def loop():
        while True:
            send_heartbeat()
            time.sleep(HEARTBEAT_INTERVAL)
    threading.Thread(target=loop, daemon=True, name="BridgeHeartbeat").start()
    logger.info(f"Heartbeat started for {WORKER_NAME}")


# ========================= HELPERS =========================
WORKER_NAME = os.getenv("WORKER_NAME", f"master-bridge-{socket.gethostname()}")

def normalize_action(position_type: int) -> str:
    return "buy" if position_type == mt5.POSITION_TYPE_BUY else "sell"


def safe_float(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def send_open_trade(position) -> bool:
    try:
        payload = {
            "ea_id": MASTER_EA_ID,
            "master_ticket": str(position.ticket),
            "symbol": str(position.symbol),
            "action": normalize_action(position.type),
            "lot_size": str(safe_float(position.volume, 0.01)),
            "sl": str(safe_float(position.sl)),
            "tp": str(safe_float(position.tp)),
            "price": str(safe_float(position.price_open)),
            "comment": position.comment or "Master trade",
        }

        response = session.post(
            f"{BACKEND_URL}/copier/open",
            json=payload,
            headers=auth_headers(),
            timeout=12,
        )

        if response.status_code in (200, 201):
            logger.info(f"✓ OPEN SENT | Ticket={position.ticket} | {position.symbol}")
            return True
        else:
            logger.error(f"✗ OPEN FAILED [{response.status_code}] Ticket={position.ticket} | {response.text}")
            return False

    except Exception as e:
        logger.error(f"OPEN SEND ERROR Ticket={position.ticket}: {e}")
        return False


def send_close_trade(ticket: str, old_data: dict) -> bool:
    try:
        payload = {
            "ea_id": MASTER_EA_ID,
            "master_ticket": str(ticket),
            "symbol": old_data["symbol"],
            "action": old_data["action"],
            "comment": "Master trade closed",
        }

        response = session.post(
            f"{BACKEND_URL}/copier/close",
            json=payload,
            headers=auth_headers(),
            timeout=12,
        )

        if response.status_code in (200, 201):
            logger.info(f"✓ CLOSE SENT | Ticket={ticket} | {old_data['symbol']}")
            return True
        else:
            logger.warning(f"✗ CLOSE FAILED [{response.status_code}] Ticket={ticket}")
            return False

    except Exception as e:
        logger.error(f"CLOSE SEND ERROR Ticket={ticket}: {e}")
        return False


# ========================= MAIN =========================
def main():
    if not all([MASTER_MT5_LOGIN, MASTER_MT5_PASSWORD, MASTER_MT5_SERVER, MASTER_API_TOKEN]):
        raise RuntimeError("Missing MASTER_ environment variables")

    if not os.path.exists(MT5_PATH):
        raise RuntimeError(f"MT5 terminal not found at: {MT5_PATH}")

    # Initialize MT5
    if not mt5.initialize(path=MT5_PATH):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    if not mt5.login(int(MASTER_MT5_LOGIN), MASTER_MT5_PASSWORD, MASTER_MT5_SERVER):
        raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")

    logger.info(f"Master MT5 Bridge Started Successfully → {MASTER_MT5_LOGIN} @ {MASTER_MT5_SERVER}")
    start_heartbeat()

    seen_positions: Dict[str, dict] = {}
    last_sent_time = {}

    positions = mt5.positions_get() or []

    for pos in positions:
        seen_positions[str(pos.ticket)] = {
            "symbol": str(pos.symbol),
            "action": normalize_action(pos.type),
            "volume": safe_float(pos.volume),
        }

    logger.info(f"Loaded {len(seen_positions)} existing positions")

    try:
        while True:
            try:

                if not mt5.terminal_info():
                    logger.warning("MT5 disconnected. Reconnecting...")

                    mt5.shutdown()
                    time.sleep(3)

                    if not mt5.initialize(path=MT5_PATH):
                        logger.error(f"MT5 reinitialize failed: {mt5.last_error()}")
                        time.sleep(10)
                        continue

                    if not mt5.login(
                        int(MASTER_MT5_LOGIN),
                        MASTER_MT5_PASSWORD,
                        MASTER_MT5_SERVER
                    ):
                        logger.error(f"MT5 relogin failed: {mt5.last_error()}")
                        time.sleep(10)
                        continue

                    logger.info("MT5 reconnected successfully")

                positions = mt5.positions_get()
                if positions is None:
                    logger.error(f"positions_get() failed: {mt5.last_error()}")
                    time.sleep(POLL_INTERVAL)
                    continue

                current_positions = {}
                new_opens = 0

                for pos in positions:
                    ticket = str(pos.ticket)
                    pos_dict = {
                        "symbol": str(pos.symbol),
                        "action": normalize_action(pos.type),
                        "volume": safe_float(pos.volume),
                    }
                    current_positions[ticket] = pos_dict

                    if ticket not in seen_positions:

                        now_ts = time.time()

                        if ticket in last_sent_time:
                            if now_ts - last_sent_time[ticket] < 10:
                                continue

                        last_sent_time[ticket] = now_ts

                        logger.info(
                            f"New Master Position → Ticket={ticket} | "
                            f"{pos.symbol} | {pos_dict['action']}"
                        )

                        if send_open_trade(pos):
                            new_opens += 1

                # Detect closed positions
                closed = set(seen_positions.keys()) - set(current_positions.keys())
                for ticket in closed:
                    send_close_trade(ticket, seen_positions[ticket])

                seen_positions = current_positions.copy()
                
                if len(last_sent_time) > 5000:
                    last_sent_time.clear()

                if new_opens > 0 or closed:
                    logger.info(f"Cycle summary: {new_opens} new opens, {len(closed)} closes")

            except Exception as e:
                logger.error(f"Error in main monitoring loop: {e}", exc_info=True)

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Master Bridge stopped by user")
    except Exception as e:
        logger.critical(f"Critical error: {e}", exc_info=True)
    finally:
        mt5.shutdown()
        logger.info("MT5 shutdown completed")


if __name__ == "__main__":
    main()