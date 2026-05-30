"""
================================================================================
  CLIENT MT5 VERIFICATION WORKER (PERIODIC RE-VERIFIER)
================================================================================
  Role: keeps verified accounts' balance/equity fresh, and retries accounts
        that failed initial inline verification.

  This is NOT the primary verification path anymore — that happens INLINE in
  the /api/client/mt5-account POST endpoint. This worker is the periodic
  refresher for already-saved accounts.

  Loop:
    1. Pull accounts grouped by priority:
         a. PENDING accounts (recent connect, retry needed)
         b. VERIFIED accounts with stale data (>5 min since last refresh)
    2. For each account: log in, fetch info, update DB
    3. Sleep
    4. Repeat

  Exponential backoff for failed accounts: 1min, 2min, 5min, 15min, 30min, 1hr.
  Verified accounts: refresh every 5 min.

  Shared MT5_LOCK serializes terminal access across this worker AND the
  client_mt5.py API endpoint (same process, same lock).
================================================================================
"""

import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Dict, List, Optional

import MetaTrader5 as mt5
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ClientMT5Account

# The Windows worker is the ONLY thing that touches MT5, so it owns its own
# terminal lock. The Render API no longer imports MetaTrader5 at all.
MT5_LOCK = threading.Lock()


# ==============================================================================
# LOGGING
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | verifier | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("verifier")


# ==============================================================================
# CONFIG
# ==============================================================================
TERMINAL_PATH = os.environ.get(
    "MT5_TERMINAL_PATH",
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
)

# How often to refresh a VERIFIED account's balance/equity
REFRESH_INTERVAL_SEC      = int(os.environ.get("VERIFY_REFRESH_SEC",   "300"))   # 5 min
# Fast loop cadence — checks for NEW connections (VERIFYING/REFRESH) often so
# users see their account details within ~1-2 seconds of tapping Connect.
LOOP_DELAY                = int(os.environ.get("VERIFY_LOOP_DELAY",    "2"))
# How many accounts to process per loop
MAX_ACCOUNTS_PER_LOOP     = int(os.environ.get("VERIFY_BATCH_SIZE",   "20"))
# Periodic MT5 refresh (full restart of terminal)
MT5_RESTART_INTERVAL_SEC  = int(os.environ.get("MT5_RESTART_INTERVAL", "3600"))  # 1 hr
# Login timeout (ms) — bounds every connect attempt so a bad broker can't hang
LOGIN_TIMEOUT_MS          = int(os.environ.get("MT5_LOGIN_TIMEOUT_MS", "15000"))  # 15s

# Failure backoff schedule (seconds since last attempt before next retry)
FAILURE_BACKOFF = [60, 120, 300, 900, 1800, 3600]

# Track per-account failure count (process-local; survives between loops only)
_failure_count: Dict[str, int]  = defaultdict(int)
_last_attempt:  Dict[str, float] = defaultdict(float)
_last_mt5_restart: float = time.time()


# ==============================================================================
# MT5 LIFECYCLE
# ==============================================================================
def init_mt5() -> bool:
    if not mt5.initialize(path=TERMINAL_PATH):
        logger.critical("MT5 INIT FAILED: %s", mt5.last_error())
        return False
    logger.info("✅ MT5 verifier connected")
    return True


def restart_mt5_if_needed() -> None:
    """Periodically restart the MT5 terminal to clean up sessions."""
    global _last_mt5_restart
    if time.time() - _last_mt5_restart > MT5_RESTART_INTERVAL_SEC:
        logger.info("🔄 Periodic MT5 restart")
        with MT5_LOCK:
            mt5.shutdown()
            time.sleep(2)
            mt5.initialize(path=TERMINAL_PATH)
        _last_mt5_restart = time.time()


def ensure_mt5_alive() -> bool:
    """Check terminal is alive; reconnect if not."""
    if not mt5.terminal_info():
        logger.warning("⚠️ MT5 disconnected — reconnecting")
        with MT5_LOCK:
            mt5.shutdown()
            time.sleep(2)
            return mt5.initialize(path=TERMINAL_PATH)
    return True


# ==============================================================================
# BACKOFF
# ==============================================================================
def next_backoff_seconds(account_login: str) -> int:
    fails = _failure_count[account_login]
    idx = min(fails, len(FAILURE_BACKOFF) - 1)
    return FAILURE_BACKOFF[idx]


def should_skip_due_to_backoff(account_login: str) -> bool:
    fails = _failure_count[account_login]
    if fails == 0:
        return False
    elapsed = time.time() - _last_attempt[account_login]
    return elapsed < next_backoff_seconds(account_login)


# ==============================================================================
# ROBUST CONNECT
# ==============================================================================
def _robust_connect(login_int: int, password: str, server: str,
                   attempts: int = 2) -> bool:
    """
    Try to connect to a user's account using multiple strategies so the
    terminal doesn't hang ("stack") when a broker server needs resolving.

    Strategy A — initialize(login=, password=, server=): passing creds to
      initialize gives the terminal the best chance to resolve the broker's
      server automatically (better than login() on its own).
    Strategy B — login(login, password, server) on the running terminal.

    Both strategies use a bounded timeout (LOGIN_TIMEOUT_MS) so a bad broker
    can never hang the worker. Each strategy is retried up to `attempts` times
    with a short pause, because server resolution can succeed on a second try.

    Returns True if connected (verified by account_info matching login).
    """
    for attempt in range(1, attempts + 1):
        # Strategy A: initialize with credentials (bounded timeout)
        try:
            ok = mt5.initialize(
                path=TERMINAL_PATH,
                login=login_int,
                password=password,
                server=server,
                timeout=LOGIN_TIMEOUT_MS,   # ms — never hangs forever
            )
            if ok:
                info = mt5.account_info()
                if info and str(info.login) == str(login_int):
                    return True
        except Exception as e:
            logger.debug("init-with-creds attempt %d failed: %s", attempt, e)

        # Strategy B: plain login on the running terminal (bounded timeout)
        try:
            if mt5.login(login_int, password=password, server=server,
                        timeout=LOGIN_TIMEOUT_MS):
                info = mt5.account_info()
                if info and str(info.login) == str(login_int):
                    return True
        except Exception as e:
            logger.debug("login attempt %d failed: %s", attempt, e)

        time.sleep(1.0)  # brief pause before next attempt

    return False


# ==============================================================================
# CORE VERIFICATION
# ==============================================================================
def verify_one_account(account: ClientMT5Account, db: Session) -> bool:
    """
    Log in, fetch info, update DB. Returns True on success.
    Holds MT5_LOCK while operating on the terminal.
    """
    login_str = str(account.login)
    _last_attempt[login_str] = time.time()

    try:
        login_int = int(account.login)
    except (ValueError, TypeError):
        logger.warning("Invalid non-numeric login: %s", account.login)
        account.verification_status = "FAILED"
        account.is_verified = False
        db.commit()
        return False

    if not MT5_LOCK.acquire(timeout=10):
        logger.warning("Couldn't acquire MT5 lock for %s", login_str)
        return False

    try:
        # ── Robust connect: try several strategies so we don't "stack" ────────
        # Strategy A: initialize() WITH credentials — best server resolution.
        #             Passing login/server to initialize lets the terminal
        #             resolve the broker server more reliably than login() alone.
        # Strategy B: plain login() on the already-running terminal.
        # Each strategy is retried briefly because server resolution can need
        # a moment on the first attempt.
        connected = _robust_connect(login_int, account.password, account.server)

        if not connected:
            err = mt5.last_error()
            logger.warning("Login failed %s on %s: %s", login_str, account.server, err)
            _failure_count[login_str] += 1
            fails = _failure_count[login_str]
            err_code = err[0] if isinstance(err, tuple) and err else 0
            err_text = (err[1] if isinstance(err, tuple) and len(err) > 1 else "") or ""

            def _set_err(msg):
                if hasattr(account, "verification_error"):
                    account.verification_error = msg

            # -2 / -3 == broker server not known to the terminal (bad server name)
            if err_code in (-2, -3):
                account.verification_status = "FAILED"
                _set_err(f"Server \"{account.server}\" not found. "
                         f"Check the exact server name in your MT5 app "
                         f"(it must match exactly, e.g. 'FBS-Demo').")
                account.is_verified = False
                db.commit()
                return False

            # "Invalid account" / authorization failures → wrong login or password.
            # MT5 commonly returns code 134/10004/10015 or text mentioning auth.
            low = err_text.lower()
            looks_like_auth = (
                err_code in (134, 10004, 10015)
                or "invalid account" in low
                or "authoriz" in low
                or "password" in low
                or "login" in low
            )
            # After 3 failed attempts with an auth-looking error, stop the silent
            # retry loop and tell the user exactly what to fix. Transient network
            # errors keep retrying (PENDING) up to the threshold.
            if looks_like_auth and fails >= 3:
                account.verification_status = "FAILED"
                _set_err("Login or password incorrect. Re-enter your MT5 "
                         "login number and the INVESTOR or MASTER password "
                         "exactly as shown in your MT5 app.")
                account.is_verified = False
                db.commit()
                return False

            # Otherwise keep PENDING for retry (transient / still settling)
            account.verification_status = "PENDING"
            if hasattr(account, "verification_error"):
                account.verification_error = None  # clear; not a hard failure yet
            account.is_verified = False
            db.commit()
            return False

        time.sleep(0.4)
        info = mt5.account_info()
        if not info:
            logger.warning("No account info for %s", login_str)
            _failure_count[login_str] += 1
            account.verification_status = "PENDING"
            account.is_verified = False
            db.commit()
            return False

        if str(info.login) != login_str:
            logger.error("Account mismatch — expected %s got %s", login_str, info.login)
            _failure_count[login_str] += 1
            account.verification_status = "PENDING"
            account.is_verified = False
            db.commit()
            return False

        # Success — capture details
        was_first_verify = not account.is_verified
        account.is_verified         = True
        account.verification_status = "VERIFIED"
        account.account_name        = str(info.name or "")
        account.broker_name         = str(info.company or "")
        account.balance             = float(info.balance or 0)
        account.equity              = float(info.equity or 0)
        account.last_verified_at    = datetime.now(timezone.utc)
        # FIX 1: clear any stale verification error from a previous failure,
        # otherwise an old error message could linger forever after a fix.
        if hasattr(account, "verification_error"):
            account.verification_error = None
        # Auto-enable AI trading on first successful verify — user connects,
        # account verifies, AI starts trading. No separate "Start AI" needed.
        if was_first_verify:
            account.ai_auto_trade = True
        db.commit()
        _failure_count[login_str] = 0
        logger.info(
            "✅ %s | %s | %s | bal=$%.2f eq=$%.2f%s",
            login_str, account.account_name, account.broker_name,
            account.balance, account.equity,
            " | AI auto-enabled" if was_first_verify else "",
        )
        return True
    except Exception as e:
        logger.error("Verify exception %s: %s", login_str, e)
        _failure_count[login_str] += 1
        return False
    finally:
        try:
            MT5_LOCK.release()
        except RuntimeError:
            pass


# ==============================================================================
# QUEUE BUILDER
# ==============================================================================
def get_accounts_to_process(db: Session) -> List[ClientMT5Account]:
    """
    Build the list of accounts that need attention this cycle.
    Priority order:
      1. NEW user actions — VERIFYING / REFRESH (no backoff, always immediate)
      2. PENDING / FAILED retries (respecting backoff)
      3. VERIFIED accounts whose last_verified_at is stale (periodic refresh)
    """
    now_utc = datetime.now(timezone.utc)
    refresh_cutoff = now_utc - timedelta(seconds=REFRESH_INTERVAL_SEC)

    # 1. HIGHEST PRIORITY — brand-new connect / refresh requests from users.
    #    These are processed every fast loop with NO backoff so the user sees
    #    their account details within ~1-2 seconds.
    fresh_requests = db.query(ClientMT5Account).filter(
        ClientMT5Account.is_active == True,
        or_(
            ClientMT5Account.verification_status == "VERIFYING",
            ClientMT5Account.verification_status == "REFRESH",
        ),
    ).limit(MAX_ACCOUNTS_PER_LOOP).all()

    # 2. Retry PENDING / FAILED / never-verified (with backoff)
    pending = db.query(ClientMT5Account).filter(
        or_(
            ClientMT5Account.verification_status == "PENDING",
            ClientMT5Account.verification_status == "FAILED",
            ClientMT5Account.verification_status.is_(None),
        ),
        ClientMT5Account.is_verified == False,
        ClientMT5Account.is_active == True,
    ).limit(MAX_ACCOUNTS_PER_LOOP).all()

    # 3. VERIFIED but stale → periodic balance refresh
    stale = db.query(ClientMT5Account).filter(
        ClientMT5Account.is_verified == True,
        ClientMT5Account.is_active == True,
        ClientMT5Account.verification_status == "VERIFIED",
        or_(
            ClientMT5Account.last_verified_at.is_(None),
            ClientMT5Account.last_verified_at < refresh_cutoff,
        ),
    ).order_by(ClientMT5Account.last_verified_at.asc().nullsfirst()).limit(MAX_ACCOUNTS_PER_LOOP).all()

    seen_logins = set()
    queue: List[ClientMT5Account] = []

    # Fresh requests first — never skipped by backoff.
    # CRITICAL: a VERIFYING/REFRESH row means the user JUST submitted (often
    # after fixing wrong details). Reset this login's failure/backoff counters
    # so the corrected credentials are tried IMMEDIATELY with a clean slate,
    # instead of being blocked for up to an hour by the old backoff schedule.
    for acc in fresh_requests:
        if acc.login in seen_logins:
            continue
        seen_logins.add(acc.login)
        login_str = str(acc.login)
        _failure_count[login_str] = 0
        _last_attempt[login_str] = 0.0
        queue.append(acc)

    # Then pending/stale, respecting backoff
    for acc in pending + stale:
        if acc.login in seen_logins:
            continue
        seen_logins.add(acc.login)
        if should_skip_due_to_backoff(str(acc.login)):
            continue
        queue.append(acc)
        if len(queue) >= MAX_ACCOUNTS_PER_LOOP:
            break

    return queue


# ==============================================================================
# MAIN LOOP
# ==============================================================================
def main_loop():
    cycle = 0
    while True:
        cycle += 1
        try:
            restart_mt5_if_needed()
            if not ensure_mt5_alive():
                logger.error("MT5 unrecoverable this cycle — sleeping")
                time.sleep(10)
                continue

            db = SessionLocal()
            try:
                queue = get_accounts_to_process(db)
                if queue:
                    logger.info("📋 Processing %d account(s)", len(queue))
                    successes = 0
                    for account in queue:
                        if verify_one_account(account, db):
                            successes += 1
                    logger.info("📊 Cycle %d: %d/%d verified", cycle, successes, len(queue))
                elif cycle % 20 == 0:
                    # Quiet heartbeat every ~100s when nothing to do
                    logger.info("💓 idle — no accounts due for verification")
            except Exception as e:
                logger.error("DB cycle error: %s", e)
                try:
                    db.rollback()
                except Exception:
                    pass
            finally:
                try:
                    db.close()
                except Exception:
                    pass
        except Exception as e:
            logger.error("Worker error: %s", e)
        time.sleep(LOOP_DELAY)


if __name__ == "__main__":
    if not init_mt5():
        raise SystemExit(1)
    logger.info("🚀 MT5 Verifier started | refresh=%ds | batch=%d | restart_every=%ds",
               REFRESH_INTERVAL_SEC, MAX_ACCOUNTS_PER_LOOP, MT5_RESTART_INTERVAL_SEC)
    main_loop()