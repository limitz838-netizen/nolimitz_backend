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

# Share the same MT5_LOCK as the API endpoint (single terminal serializer).
# If imported from a different process, this becomes its own lock — which is
# fine since the API and worker would each have their own terminal then.
try:
    from app.routes.client_mt5 import _MT5_LOCK as MT5_LOCK
except Exception:
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
# Loop cadence
LOOP_DELAY                = int(os.environ.get("VERIFY_LOOP_DELAY",    "5"))
# How many accounts to process per loop
MAX_ACCOUNTS_PER_LOOP     = int(os.environ.get("VERIFY_BATCH_SIZE",   "20"))
# Periodic MT5 refresh (full restart of terminal)
MT5_RESTART_INTERVAL_SEC  = int(os.environ.get("MT5_RESTART_INTERVAL", "3600"))  # 1 hr

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
        if not mt5.login(login_int, password=account.password, server=account.server):
            err = mt5.last_error()
            logger.warning("Login failed %s: %s", login_str, err)
            _failure_count[login_str] += 1
            # Don't mark as FAILED forever — keep PENDING for retry
            account.verification_status = "PENDING"
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
        account.is_verified         = True
        account.verification_status = "VERIFIED"
        account.account_name        = str(info.name or "")
        account.broker_name         = str(info.company or "")
        account.balance             = float(info.balance or 0)
        account.equity              = float(info.equity or 0)
        account.last_verified_at    = datetime.now(timezone.utc)
        db.commit()
        _failure_count[login_str] = 0
        logger.info(
            "✅ %s | %s | %s | bal=$%.2f eq=$%.2f",
            login_str, account.account_name, account.broker_name,
            account.balance, account.equity,
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
    Priority:
      1. PENDING accounts past their backoff
      2. VERIFIED accounts whose last_verified_at is older than REFRESH_INTERVAL_SEC
    """
    now_utc = datetime.now(timezone.utc)
    refresh_cutoff = now_utc - timedelta(seconds=REFRESH_INTERVAL_SEC)

    # PENDING accounts (or never-verified) get tried first
    pending = db.query(ClientMT5Account).filter(
        or_(
            ClientMT5Account.verification_status == "PENDING",
            ClientMT5Account.verification_status == "FAILED",
            ClientMT5Account.verification_status.is_(None),
            ClientMT5Account.is_verified == False,
        ),
        ClientMT5Account.is_active == True,
    ).limit(MAX_ACCOUNTS_PER_LOOP).all()

    # VERIFIED but stale
    stale = db.query(ClientMT5Account).filter(
        ClientMT5Account.is_verified == True,
        ClientMT5Account.is_active == True,
        or_(
            ClientMT5Account.last_verified_at.is_(None),
            ClientMT5Account.last_verified_at < refresh_cutoff,
        ),
    ).order_by(ClientMT5Account.last_verified_at.asc().nullsfirst()).limit(MAX_ACCOUNTS_PER_LOOP).all()

    seen_logins = set()
    queue: List[ClientMT5Account] = []
    for acc in pending + stale:
        if acc.login in seen_logins:
            continue
        seen_logins.add(acc.login)
        # Apply backoff to pending failures
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