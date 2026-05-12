import os
import shutil
from pathlib import Path

BASE_MT5_PATH = r"C:\Program Files\MetaTrader 5"
BASE_TERMINAL_EXE = os.path.join(BASE_MT5_PATH, "terminal64.exe")

TERMINALS_ROOT = Path("terminals")
TERMINALS_ROOT.mkdir(exist_ok=True)


def get_license_terminal_dir(license_id: int) -> Path:
    return TERMINALS_ROOT / f"license_{license_id}"


def ensure_terminal_for_license(license_id: int):
    """
    Creates isolated MT5 terminal folder for a license.
    """

    terminal_dir = get_license_terminal_dir(license_id)

    if not terminal_dir.exists():
        terminal_dir.mkdir(parents=True)

    target_terminal = terminal_dir / "terminal64.exe"

    # copy terminal if missing
    if not target_terminal.exists():
        shutil.copy2(BASE_TERMINAL_EXE, target_terminal)

    return {
        "terminal_path": str(target_terminal),
        "data_path": str(terminal_dir),
    }