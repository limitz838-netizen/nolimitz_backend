@echo off
REM ============================================================
REM  start_shards.bat  -  launches every shard, staggered
REM
REM  Change SHARDS below to match how many terminal folders
REM  you cloned. Start with 2. Only go higher once 2 works.
REM
REM  IMPORTANT: stop your existing single worker FIRST.
REM  Running both at once means the same account gets traded
REM  twice - double lot size, real money lost.
REM ============================================================

set SHARDS=2

echo.
echo  ============================================================
echo   STARTING %SHARDS% SHARDS
echo  ============================================================
echo.
echo   Before continuing, confirm:
echo     [ ] The old single worker is STOPPED
echo     [ ] You cloned %SHARDS% terminal folders under C:\MT5\
echo     [ ] You are testing on DEMO accounts
echo.
pause

setlocal enabledelayedexpansion
set /a LAST=%SHARDS%-1

for /l %%i in (0,1,!LAST!) do (
    echo  launching shard %%i ...
    start "Nolimitz Shard %%i" cmd /k "%~dp0run_shard.bat" %%i %SHARDS%

    REM MT5 terminals are slow and disk-heavy on first launch.
    REM Starting them all at once thrashes the disk and some will
    REM time out. 25 seconds apart is safe.
    if not %%i==!LAST! (
        echo  waiting 25s before the next one...
        timeout /t 25 /nobreak >nul
    )
)

echo.
echo  All %SHARDS% shards launched in separate windows.
echo.
echo  Watch each window for:  "Trader MT5 connected"
echo  Then watch for:         "Cycle N took Xs for Y accounts"
echo.
echo  To stop everything: close each shard window, or run stop_shards.bat
echo.
pause
