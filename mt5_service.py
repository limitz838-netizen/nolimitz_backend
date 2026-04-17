from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import MetaTrader5 as mt5
import traceback
import uvicorn
import time
import threading
import os
import subprocess

app = FastAPI(title="MT5 Verification Service")

mt5_lock = threading.Lock()

MT5_TERMINAL_PATH = r"C:\Users\user\Desktop\NolimitzMT5Verifier\terminal64.exe"
VERIFIER_SECRET = "nolimitz_mt5_secret_2026"


class MT5VerifyRequest(BaseModel):
    login: str
    password: str
    server: str


@app.get("/")
def root():
    return {"message": "MT5 verification service is running"}


def safe_shutdown():
    try:
        mt5.shutdown()
    except Exception:
        pass


def force_kill_terminal():
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "terminal64.exe"],
            capture_output=True,
            text=True,
        )
    except Exception:
        pass


def hard_reset_terminal():
    safe_shutdown()
    time.sleep(1.5)
    force_kill_terminal()
    time.sleep(3.0)


def normalize_error_message(error_info):
    text = str(error_info).lower()

    if "authorization failed" in text or "invalid account" in text:
        return "Wrong MT5 login, password, or server. Please check your account details and try again."

    if "no connection" in text:
        return "Could not connect to broker server. Please check your internet or broker server name."

    if "ipc timeout" in text:
        return "MT5 terminal took too long to respond. Please restart the MT5 verifier terminal and try again."

    if "initialize failed" in text:
        return f"MT5 initialize failed: {error_info}"

    return f"MT5 verification failed: {error_info}"


def initialize_terminal(final_terminal_path: str):
    initialized = False
    last_init_error = None

    for attempt in range(2):
        initialized = mt5.initialize(
            path=final_terminal_path,
            timeout=60000,
            portable=True,
        )

        if initialized:
            return True, None

        last_init_error = mt5.last_error()
        safe_shutdown()
        time.sleep(3.0 if attempt == 0 else 2.0)

    return False, last_init_error


def verify_mt5_credentials_direct(
    mt_login: str,
    mt_password: str,
    mt_server: str,
    terminal_path: str | None = None,
):
    with mt5_lock:
        login_value = str(mt_login).strip()
        password_value = str(mt_password).strip()
        server_value = str(mt_server).strip()
        final_terminal_path = terminal_path or MT5_TERMINAL_PATH

        if not login_value or not password_value or not server_value:
            raise Exception("Login, password, and server are required.")

        try:
            login_int = int(login_value)
        except ValueError:
            raise Exception("Login must be a valid number.")

        if not os.path.exists(final_terminal_path):
            raise Exception(f"MT5 terminal not found at: {final_terminal_path}")

        hard_reset_terminal()

        initialized, last_init_error = initialize_terminal(final_terminal_path)
        if not initialized:
            raise Exception(f"MT5 initialize failed: {last_init_error}")

        time.sleep(2.0)

        authorized = mt5.login(
            login=login_int,
            password=password_value,
            server=server_value,
            timeout=60000,
        )

        if not authorized:
            error_info = mt5.last_error()
            hard_reset_terminal()
            raise Exception(normalize_error_message(error_info))

        time.sleep(1.5)

        account_info = mt5.account_info()
        if account_info is None:
            error_info = mt5.last_error()
            hard_reset_terminal()
            raise Exception(normalize_error_message(error_info))

        if str(account_info.login) != str(login_int):
            hard_reset_terminal()
            raise Exception("Wrong MT5 login, password, or server. Please check your account details and try again.")

        result = {
            "success": True,
            "message": "MT5 account connected successfully",
            "login": str(account_info.login),
            "server": account_info.server,
            "balance": str(account_info.balance),
            "equity": str(account_info.equity),
            "name": account_info.name,
            "broker_name": account_info.company,
        }

        hard_reset_terminal()
        return result


@app.post("/verify-mt5")
def verify_mt5(
    data: MT5VerifyRequest,
    x_api_key: str = Header(None),
):
    if x_api_key != VERIFIER_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        return verify_mt5_credentials_direct(
            mt_login=data.login,
            mt_password=data.password,
            mt_server=data.server,
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=401, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("mt5_service:app", host="0.0.0.0", port=8011, reload=False)