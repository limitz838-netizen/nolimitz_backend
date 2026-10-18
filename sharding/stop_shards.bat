@echo off
REM ============================================================
REM  stop_shards.bat  -  stops every shard worker
REM
REM  Stops the Python workers. Leaves MT5 terminals running
REM  (they're harmless idle, and restarting them is slow).
REM
REM  Use -all to also close the terminals.
REM ============================================================

echo.
echo  Stopping shard workers...

powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*ai_execution_worker*' } | ForEach-Object { Write-Host ('  stopping PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"

echo.

if /i "%~1"=="-all" (
    echo  Also closing MT5 terminals...
    powershell -NoProfile -Command ^
      "Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object { $_.Path -like 'C:\MT5\shard*' } | Stop-Process -Force"
    echo.
)

echo  Done.
echo.
pause
