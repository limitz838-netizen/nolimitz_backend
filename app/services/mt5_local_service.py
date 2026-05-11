import shutil
from pathlib import Path
import MetaTrader5 as mt5


BASE_DIR = Path(r"C:\Users\user\Desktop\Nolimitz")
TERMINALS_DIR = BASE_DIR / "terminals"


class MT5LocalService:

    def __init__(self):
        pass


    def create_terminal(self):

        existing = [
            p for p in TERMINALS_DIR.iterdir()
            if p.is_dir() and p.name.startswith("terminal_")
        ]

        next_id = len(existing) + 1

        new_name = f"terminal_{next_id:03}"

        source = TERMINALS_DIR / "terminal_001"
        destination = TERMINALS_DIR / new_name

        shutil.copytree(source, destination)

        return new_name


    def connect_account(
        self,
        terminal_name,
        login,
        password,
        server
    ):

        terminal_path = (
            TERMINALS_DIR
            / terminal_name
            / "terminal64.exe"
        )

        connected = mt5.initialize(
            path=str(terminal_path),
            login=login,
            password=password,
            server=server,
            timeout=60000,
        )

        if not connected:

            return {
                "success": False,
                "error": mt5.last_error()
            }

        account = mt5.account_info()

        return {
            "success": True,
            "login": account.login,
            "server": account.server,
            "balance": account.balance,
            "terminal": terminal_name,
        }