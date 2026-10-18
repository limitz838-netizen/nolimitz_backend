@echo off
REM ============================================================
REM  run_shard.bat  -  starts ONE worker process (one shard)
REM
REM  Usage:  run_shard.bat <index> <total>
REM  Example: run_shard.bat 0 2     (shard 0 of 2)
REM
REM  Do not run this directly for normal operation —
REM  use start_shards.bat, which launches all of them.
REM ============================================================

if "%~2"=="" (
    echo Usage: run_shard.bat ^<index^> ^<total^>
    echo Example: run_shard.bat 0 2
    pause
    exit /b 1
)

REM ---- EDIT THIS if your repo lives somewhere else ----
set REPO=C:\Users\user\Desktop\nolimitz_backend

REM ---- Shard identity --------------------------------
set SHARD_INDEX=%~1
set SHARD_TOTAL=%~2

REM ---- This shard's own MT5 terminal ------------------
set MT5_TERMINAL_PATH=C:\MT5\shard%~1\terminal64.exe
set MT5_PORTABLE=true

REM ---- Separate heartbeat identity per shard ----------
set WORKER_NAME=trader-shard%~1

title Nolimitz Trader - Shard %~1 of %~2

echo.
echo  ============================================
echo   SHARD %~1 of %~2
echo   terminal: %MT5_TERMINAL_PATH%
echo  ============================================
echo.

cd /d "%REPO%"
py -3.10 -m app.ai.ai_execution_worker

echo.
echo  Shard %~1 stopped. Press any key to close.
pause >nul
