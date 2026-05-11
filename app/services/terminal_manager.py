import os
import subprocess
import time
import MetaTrader5 as mt5

BASE_TERMINAL_PATH = r"C:\Users\user\Desktop\NolimitzTerminals"


class TerminalManager:

    def __init__(self):
        self.base_path = BASE_TERMINAL_PATH

    def login_terminal(self, terminal_name, login, password, server):

        terminal_path = os.path.join(
            self.base_path,
            terminal_name,
            "terminal64.exe"
        )

        if not os.path.exists(terminal_path):
            raise Exception(f"Terminal not found: {terminal_name}")

        # Start MT5 terminal
        subprocess.Popen([terminal_path])

        # Wait for terminal to boot
        time.sleep(5)

        # Initialize MT5 connection
        initialized = mt5.initialize(path=terminal_path)

        if not initialized:
            raise Exception(f"MT5 initialize failed: {mt5.last_error()}")

        # Login to broker
        authorized = mt5.login(
            login=int(login),
            password=password,
            server=server
        )

        if not authorized:
            raise Exception(f"MT5 login failed: {mt5.last_error()}")

        # Get account info
        account_info = mt5.account_info()

        if account_info is None:
            raise Exception("Failed to get account info")

        return {
            "success": True,
            "login": account_info.login,
            "balance": account_info.balance,
            "equity": account_info.equity,
            "server": account_info.server,
            "name": account_info.name,
        }