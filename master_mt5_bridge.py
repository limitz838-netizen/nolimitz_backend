import time
from typing import Any, Dict, Optional, Set

import MetaTrader5 as mt5
import requests

# =========================
# CONFIG
# =========================
MT5_PATH = r"C:\Users\user\Desktop\NolimitzMT5Verifier\terminal64.exe"

# ✅ FIXED API
API_URL = "https://nolimitz-backend-yfne.onrender.com"

ADMIN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhZG1pbl9pZCI6MiwiZW1haWwiOiIxMjNsaW1pdHpAZ21haWwuY29tIiwicm9sZSI6ImFkbWluIiwiZXhwIjoxNzc2MTk4MDM4fQ.KLzgrYTGKHRC66pYWDC5Ede3uEZliN7VJIGzx2dn5KI"

POLL_SECONDS = 2
CONFIG_REFRESH_SECONDS = 15
REQUEST_TIMEOUT = 20

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {ADMIN_TOKEN}",
}

known_tickets: Set[int] = set()
last_config_fetch_at = 0.0
master_config: Optional[Dict[str, Any]] = None


# =========================
# BACKEND CONFIG
# =========================
def fetch_master_account_status():
    try:
        res = requests.get(
            f"{API_URL}/admin/master-account/status",
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if not res.ok:
            print("Failed to fetch config ❌", res.text)
            return None

        return res.json()

    except Exception as e:
        print("Error fetching config:", e)
        return None


def notify_backend_connected(account_name: str, broker_name: str):
    try:
        res = requests.post(
            f"{API_URL}/admin/master-account/connected",
            json={
                "account_name": account_name,
                "broker_name": broker_name,
            },
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        print("Backend marked connected ✅", res.json())

    except Exception as e:
        print("Failed to notify backend:", e)


def refresh_master_config(force=False):
    global last_config_fetch_at, master_config

    now = time.time()

    if not force and master_config and (now - last_config_fetch_at < CONFIG_REFRESH_SECONDS):
        return True

    data = fetch_master_account_status()
    last_config_fetch_at = now

    if not data:
        return False

    ea_id = data.get("ea_id")
    mt_login = data.get("mt_login")
    mt_password = data.get("mt_password")
    mt_server = data.get("mt_server")

    if not ea_id or not mt_login or not mt_password or not mt_server:
        print("Master account not ready yet ❌")
        master_config = None
        return False

    master_config = {
        "ea_id": int(ea_id),
        "mt_login": str(mt_login),
        "mt_password": str(mt_password),
        "mt_server": str(mt_server),
    }

    print("Loaded config ✅", master_config)
    return True


# =========================
# CONNECT MT5
# =========================
def connect_mt5():
    global known_tickets

    if not refresh_master_config(force=True):
        return False

    mt5.shutdown()
    time.sleep(1)

    if not mt5.initialize(path=MT5_PATH):
        print("MT5 init failed:", mt5.last_error())
        return False

    authorized = mt5.login(
        int(master_config["mt_login"]),
        password=master_config["mt_password"],
        server=master_config["mt_server"],
    )

    if not authorized:
        print("Login failed ❌", mt5.last_error())
        mt5.shutdown()
        return False

    account_info = mt5.account_info()

    print(f"Connected to MT5 ✅ {account_info.login}")

    # ✅ VERY IMPORTANT
    notify_backend_connected(
        account_name=account_info.name or f"Master {account_info.login}",
        broker_name=account_info.server,
    )

    known_tickets.clear()
    return True


def ensure_mt5_connection():
    if mt5.terminal_info() is None:
        print("Reconnecting MT5...")
        return connect_mt5()
    return True


# =========================
# SEND EVENTS
# =========================
def send_open_trade(pos):
    payload = {
        "ea_id": master_config["ea_id"],  # ✅ FIXED
        "master_ticket": str(pos.ticket),
        "symbol": pos.symbol,
        "action": "buy" if pos.type == 0 else "sell",
        "sl": str(pos.sl or 0),
        "tp": str(pos.tp or 0),
        "price": str(pos.price_open or 0),
        "comment": pos.comment or "MASTER TRADE",
    }

    try:
        res = requests.post(
            f"{API_URL}/copier/open",
            json=payload,
            headers=HEADERS,
        )
        print("OPEN SENT ✅", res.json())
    except Exception as e:
        print("Open error:", e)


def send_close_trade(ticket, symbol):
    payload = {
        "ea_id": master_config["ea_id"],  # ✅ FIXED
        "master_ticket": str(ticket),
        "symbol": symbol,
        "comment": "closed by master",
    }

    try:
        res = requests.post(
            f"{API_URL}/copier/close",
            json=payload,
            headers=HEADERS,
        )
        print("CLOSE SENT ✅", res.json())
    except Exception as e:
        print("Close error:", e)


# =========================
# LOOP
# =========================
def run():
    global known_tickets

    last_positions = {}

    while True:
        refresh_master_config()

        if not ensure_mt5_connection():
            time.sleep(5)
            continue

        positions = mt5.positions_get()
        current = set()
        current_map = {}

        if positions is None:
            time.sleep(2)
            continue

        for pos in positions:
            current.add(pos.ticket)
            current_map[pos.ticket] = pos

            if pos.ticket not in known_tickets:
                print("NEW TRADE", pos.ticket)
                send_open_trade(pos)

        closed = known_tickets - current
        for t in closed:
            old = last_positions.get(t)
            symbol = old.symbol if old else ""
            print("CLOSED", t)
            send_close_trade(t, symbol)

        known_tickets = current
        last_positions = current_map

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    if connect_mt5():
        run()
    else:
        print("Bridge failed to start ❌")