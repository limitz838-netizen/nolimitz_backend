r"""Clear a stuck MetaTrader 5 self-update for one terminal (step 75, Nov 2026).

Seen on 9 Nov 2026: MetaQuotes shipped a new MT5 build. A terminal that starts while an older copy of
its updater is still alive fails with "LiveUpdate failed to create copy ... [32]" (file in use), hands
over to that dead updater and exits - every time. The verifier and copier lane 5 were down for hours,
and every restart left one more stuck updater behind (lane 5 had ~140).

clear_stuck_update(terminal_path) finds the terminal's MetaQuotes data folder(s) (origin.txt names the
install folder), stops updater processes running from <data>\liveupdate that are older than
MIN_AGE_SEC (a real update finishes in well under a minute, so a young one is left alone), and then
deletes the leftover liveupdate folder so the next start can update cleanly. Never touches any
terminal64.exe outside a liveupdate folder. Windows only; a no-op anywhere else. Never raises.
"""
import logging
import os
import subprocess

log = logging.getLogger("mt5.liveupdate")
MIN_AGE_SEC = int(os.getenv("MT5_STUCK_UPDATE_AGE_SEC", "180"))

_PS = r"""
$dir = '{dir}'.TrimEnd('\')
$root = Join-Path $env:APPDATA 'MetaQuotes\Terminal'
if (-not (Test-Path $root)) {{ $root = 'C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal' }}
$killed = 0; $cleared = 0
Get-ChildItem $root -Directory -ErrorAction SilentlyContinue | ForEach-Object {{
  $o = Join-Path $_.FullName 'origin.txt'
  if (-not (Test-Path $o)) {{ return }}
  $origin = ((Get-Content $o -Raw -ErrorAction SilentlyContinue) -replace '\s+$','').TrimEnd('\')
  if ($origin -ne $dir) {{ return }}
  $lu = Join-Path $_.FullName 'liveupdate'
  $cut = (Get-Date).AddSeconds(-{age})
  Get-Process -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -and $_.Path.StartsWith($lu + '\', 'OrdinalIgnoreCase') -and $_.StartTime -lt $cut }} |
    ForEach-Object {{ Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue; $killed++ }}
  Start-Sleep -Seconds 2
  $left = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -and $_.Path.StartsWith($lu + '\', 'OrdinalIgnoreCase') }}).Count
  if ($left -eq 0 -and (Test-Path $lu)) {{ Remove-Item $lu -Recurse -Force -ErrorAction SilentlyContinue; if (-not (Test-Path $lu)) {{ $cleared++ }} }}
}}
"$killed $cleared"
"""


def clear_stuck_update(terminal_path: str) -> tuple:
    """Returns (stuck updaters stopped, update folders cleared). (0, 0) when nothing to do or not Windows."""
    if os.name != "nt" or not terminal_path:
        return 0, 0
    folder = os.path.dirname(os.path.abspath(terminal_path)).replace("'", "''")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", _PS.format(dir=folder, age=MIN_AGE_SEC)],
                           timeout=60, capture_output=True, text=True)
        parts = (r.stdout or "").strip().split()
        killed, cleared = (int(parts[-2]), int(parts[-1])) if len(parts) >= 2 else (0, 0)
        if killed or cleared:
            log.warning("cleared stuck MT5 update for %s: %d updater(s) stopped, %d folder(s) removed",
                        terminal_path, killed, cleared)
        return killed, cleared
    except Exception as e:
        log.warning("stuck-update check failed for %s: %s", terminal_path, e)
        return 0, 0
