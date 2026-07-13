"""
================================================================================
  CLIENT MT5 VERIFICATION WORKER (PERIODIC RE-VERIFIER)
================================================================================
  Role: keeps verified accounts' balance/equity fresh, and retries accounts
        that failed initial inline verification.

  This is NOT the primary verification path anymore — that happens INLINE in
  the /api/client/mt5-account POST endpoint. This worker is the periodic
  refresher for already-saved accounts.

  ── WHAT CHANGED (smooth-operation rewrite) ──────────────────────────────────
  The old worker could stall the whole queue when a user entered wrong details:
  it re-initialized the terminal per account (heavy / destabilizing), retried a
  wrong password 3× before failing, and rebuilt the terminal on any hiccup.

  This version:
    • Logs in with mt5.login() on a terminal initialized ONCE at startup
      (light; doesn't destabilize the terminal when cycling many accounts).
    • Bounds every attempt with a short hard timeout (MT5_LOGIN_TIMEOUT_MS).
    • FAILS FAST on clearly-wrong details (bad server / bad login / bad
      password) on the FIRST attempt, and FAILED accounts are skipped by the
      queue builder — so one bad account can never interrupt the others.
    • Treats only ambiguous errors (timeout / dropped connection / IPC loss)
      as transient: retried with backoff up to a cap, then failed — so a GOOD
      account is never permanently killed by a momentary network blip.
    • Rebuilds the terminal only on a real IPC pipe death, once, then continues.
    • Honours a per-loop time budget so the loop always stays responsive to
      fresh VERIFYING/REFRESH requests.

  NOTE: full isolation from the execution worker (so a verifier hiccup can
  never touch the trading terminal) comes from running the two workers on
  SEPARATE MT5 installs — that's the next task.

  Loop:
    1. Pull accounts grouped by priority (fresh requests → pending → stale).
    2. For each account: log in, fetch info, update DB. Skip cleanly on bad
       details.
    3. Sleep. Repeat.

  Exponential backoff for transient failures: 1min, 2min, 5min, 15min, 30min, 1hr.
  Verified accounts: refresh every 5 min.
================================================================================
"""

import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

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
# the worker. Kept short on purpose: a single slow/wrong server costs at most
# this long, once, before the account is skipped.
LOGIN_TIMEOUT_MS          = int(os.environ.get("MT5_LOGIN_TIMEOUT_MS", "8000"))   # 8s
# A COLD terminal relaunch (after a crash / IPC death) needs far longer than a
# warm login — 8s isn't enough for terminal64.exe to start AND re-establish the
# IPC pipe, which is why recovery kept failing. Use a patient timeout + retries.
INIT_TIMEOUT_MS           = int(os.environ.get("MT5_INIT_TIMEOUT_MS", "30000"))   # 30s
INIT_RETRIES              = int(os.environ.get("MT5_INIT_RETRIES", "3"))
# How many TRANSIENT (non-deterministic) failures before we give up on an
# account and mark it FAILED. Protects a GOOD account from being permanently
# failed by a momentary network blip, while still bounding pointless churn.
MAX_TRANSIENT_RETRIES     = int(os.environ.get("VERIFY_MAX_TRANSIENT_RETRIES", "4"))
# Per-loop wall-clock budget. If processing the queue takes longer than this,
# defer the rest to the next loop so fresh VERIFYING/REFRESH requests from
# users stay responsive instead of waiting behind a pile of slow accounts.
LOOP_TIME_BUDGET_SEC      = int(os.environ.get("VERIFY_LOOP_BUDGET_SEC", "45"))

# Failure backoff schedule (seconds since last attempt before next retry)
FAILURE_BACKOFF = [60, 120, 300, 900, 1800, 3600]

# Track per-account failure count (process-local; survives between loops only)
_failure_count: Dict[str, int]  = defaultdict(int)
_last_attempt:  Dict[str, float] = defaultdict(float)
_last_mt5_restart: float = time.time()


# ==============================================================================
# DB HELPER
# ==============================================================================
def _safe_commit(db: Session) -> bool:
    """Commit, swallowing/rolling-back on error so one bad write never crashes
    the loop."""
    try:
        db.commit()
        return True
    except Exception as e:
        logger.error("DB commit failed: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return False


# ==============================================================================
# MT5 LIFECYCLE
# ==============================================================================
def init_mt5() -> bool:
    if not mt5.initialize(path=TERMINAL_PATH, timeout=LOGIN_TIMEOUT_MS):
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
            mt5.initialize(path=TERMINAL_PATH, timeout=LOGIN_TIMEOUT_MS)
        _last_mt5_restart = time.time()


def _init_with_retries() -> bool:
    """Bring the terminal up, allowing a cold start time to establish the pipe.
    Retries with growing waits so a freshly-relaunched (or crashed) terminal
    isn't given up on after a single short timeout."""
    for attempt in range(1, INIT_RETRIES + 1):
        try:
            mt5.shutdown()
        except Exception:
            pass
        time.sleep(2 * attempt)        # 2s, 4s, 6s — let the process die/relaunch
        try:
            if mt5.initialize(path=TERMINAL_PATH, timeout=INIT_TIMEOUT_MS):
                if attempt > 1:
                    logger.info("✅ MT5 recovered on init attempt %d", attempt)
                return True
        except Exception as e:
            logger.warning("init attempt %d exception: %s", attempt, e)
        logger.warning("init attempt %d/%d failed: %s",
                       attempt, INIT_RETRIES, mt5.last_error())
    return False


def ensure_mt5_alive() -> bool:
    """Check terminal is alive; reconnect (with patient retries) if not."""
    if not mt5.terminal_info():
        logger.warning("⚠️ MT5 disconnected — reconnecting")
        with MT5_LOCK:
            return _init_with_retries()
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
# TERMINAL HEALTH (IPC)
# ==============================================================================
def _ipc_is_dead() -> bool:
    """True if the last MT5 error is an IPC/terminal pipe failure.
    The old check only caught -10004; the real wedger in the logs is -10001
    ('IPC send failed') — a login to a dead/unknown server hangs the terminal
    and breaks the pipe. Catch the whole internal-IPC family so the terminal
    actually gets rebuilt instead of the worker looping 'unrecoverable'."""
    try:
        err = mt5.last_error()
        return (isinstance(err, tuple) and err
                and err[0] in (-10001, -10002, -10003, -10004))
    except Exception:
        return False


def _rebuild_terminal() -> bool:
    """
    Fully tear down and restart the MT5 terminal connection. Needed when the
    IPC pipe dies (-10004), which can happen when rapidly switching between
    different brokers/servers. A plain login() retry on a dead pipe can NEVER
    succeed — the pipe itself must be rebuilt first.
    """
    try:
        mt5.shutdown()
    except Exception:
        pass
    time.sleep(1.5)
    ok = _init_with_retries()
    if ok:
        logger.info("🔄 Rebuilt MT5 terminal after IPC loss")
    else:
        logger.warning("⚠️ Terminal rebuild failed: %s", mt5.last_error())
    return ok


# ==============================================================================
# FAILURE CLASSIFICATION
# ==============================================================================
def _classify_login_failure(err) -> str:
    """
    Map an mt5.last_error() tuple to one of:
      'bad_auth'   — wrong login number or password (deterministic → skip now)
      'bad_server' — server name unknown to the terminal (deterministic → skip)
      'transient'  — timeout / dropped connection / broker outage (retry-able)

    We are deliberately CONSERVATIVE: only clearly-deterministic errors are
    classified as bad_auth / bad_server. Everything ambiguous falls through to
    'transient', so a momentary blip can never permanently fail a GOOD account.
    """
    code = err[0] if isinstance(err, tuple) and err else 0
    text = ((err[1] if isinstance(err, tuple) and len(err) > 1 else "") or "").lower()

    # Authorization problems = wrong login or password. Retrying never helps.
    if (code in (-6, 134, 10004, 10015)
            or "authoriz" in text
            or "invalid account" in text
            or "account disabled" in text
            or "password" in text):
        return "bad_auth"

    # Server name not known to the terminal = wrong/misspelled server.
    if (code in (-2, -3)
            or "not found" in text
            or "unknown" in text
            or "invalid server" in text
            or "no such server" in text):
        return "bad_server"

    # Timeouts, connection drops, IPC loss, temporary broker outages, etc.
    return "transient"


# ==============================================================================
# CONNECT (login-only on a terminal that's already initialized)
# ==============================================================================
def _connect_account(login_int: int, password: str, server: str) -> Tuple[bool, str]:
    """
    Switch the (already-initialized) terminal to this account via login().

    login() is far lighter than re-initialize() and is much less likely to
    destabilize the terminal when cycling through many accounts back to back.

    Returns (connected, failure_kind):
      connected=True, kind=""                 → logged in & account_info matches
      connected=False, kind='bad_auth'        → wrong login/password (skip now)
      connected=False, kind='bad_server'      → wrong server name (skip now)
      connected=False, kind='transient'       → retry-able

    Bounded by LOGIN_TIMEOUT_MS so a single bad/slow broker can never hang the
    worker. On a real IPC pipe death (-10004) the terminal is rebuilt ONCE and
    a single clean retry is attempted before giving up (transient).
    """
    try:
        ok = mt5.login(login_int, password=password, server=server,
                       timeout=LOGIN_TIMEOUT_MS)
        if ok:
            info = mt5.account_info()
            if info and str(info.login) == str(login_int):
                return True, ""
            # Logged in but no/mismatched account info — treat as transient.
            return False, "transient"

        err = mt5.last_error()

        # IPC pipe dead → rebuild the terminal once, then one clean retry.
        # If it STILL dies on this same account, this account's server is what's
        # wedging the terminal — flag it 'ipc_wedge' so it gets failed fast and
        # removed from the queue instead of re-crashing the terminal every cycle.
        if _ipc_is_dead():
            if _rebuild_terminal():
                if mt5.login(login_int, password=password, server=server,
                             timeout=LOGIN_TIMEOUT_MS):
                    info = mt5.account_info()
                    if info and str(info.login) == str(login_int):
                        return True, ""
            return False, "ipc_wedge"
        
        logger.warning(
            "MT5 login failed | login=%s | server=%s | error=%s",
            login_int,
            server,
            err
        )

        return False, _classify_login_failure(err)

    except Exception as e:
        logger.debug("connect exception for %s: %s", login_int, e)
        return False, "transient"


# ==============================================================================
# TRANSIENT HANDLING
# ==============================================================================
def _handle_transient(account: ClientMT5Account, db: Session,
                      login_str: str, reason: str) -> None:
    """
    Record a transient failure. Retries with backoff up to MAX_TRANSIENT_RETRIES,
    then marks the account FAILED (so the queue stops retrying it) with a
    message telling the user to check their connection / broker and resubmit.
    """
    _failure_count[login_str] += 1
    fails = _failure_count[login_str]

    if fails >= MAX_TRANSIENT_RETRIES:
        account.verification_status = "FAILED"
        account.is_verified = False
        if hasattr(account, "verification_error"):
            account.verification_error = (
                "Couldn't connect after several attempts. Check your internet "
                "and that your broker's server is online, then tap Connect to retry."
            )
        logger.warning("⛔ %s FAILED after %d transient attempt(s) (%s)",
                       login_str, fails, reason)
    else:
        account.verification_status = "PENDING"
        account.is_verified = False
        if hasattr(account, "verification_error"):
            account.verification_error = None  # not a hard failure yet
        logger.info("… %s transient (%s) — retry %d/%d with backoff",
                    login_str, reason, fails, MAX_TRANSIENT_RETRIES)

    _safe_commit(db)


# ==============================================================================
# CORE VERIFICATION
# ==============================================================================
def verify_one_account(account: ClientMT5Account, db: Session) -> bool:
    """
    Log in, fetch info, update DB. Returns True on success.
    Holds MT5_LOCK only for the bounded login + info read.

    Bad details (wrong server / login / password) are marked FAILED on the
    FIRST attempt and then skipped by the queue builder, so they never stall
    the rest of the queue.
    """
    login_str = str(account.login)
    _last_attempt[login_str] = time.time()

    # Non-numeric login can never be valid — fail immediately, no MT5 call.
    try:
        login_int = int(account.login)
    except (ValueError, TypeError):
        logger.info("⛔ %s FAILED (login is not numeric) — skipping", login_str)
        account.verification_status = "FAILED"
        account.is_verified = False
        if hasattr(account, "verification_error"):
            account.verification_error = (
                "Login must be your numeric MT5 account number "
                "(digits only, e.g. 51234567)."
            )
        _safe_commit(db)
        return False

    # Bounded lock acquire — if the terminal is busy, just retry next loop
    # rather than blocking. (Short timeout keeps the loop responsive.)
    if not MT5_LOCK.acquire(timeout=5):
        logger.warning("Couldn't acquire MT5 lock for %s — will retry next loop", login_str)
        return False

    try:
        connected, kind = _connect_account(login_int, account.password, account.server)

        # ── SUCCESS ──────────────────────────────────────────────────────────
        if connected:
            time.sleep(0.2)
            info = mt5.account_info()
            if not info or str(info.login) != login_str:
                _handle_transient(account, db, login_str,
                                  "account info unavailable after login")
                return False

            was_first_verify = not account.is_verified
            account.is_verified         = True
            account.verification_status = "VERIFIED"
            account.account_name        = str(info.name or "")
            account.broker_name         = str(info.company or "")
            account.balance             = float(info.balance or 0)
            account.equity              = float(info.equity or 0)
            account.last_verified_at    = datetime.now(timezone.utc)
            # Clear any stale error from a previous failed attempt.
            if hasattr(account, "verification_error"):
                account.verification_error = None
            # Auto-enable AI trading on first successful verify.
            if was_first_verify:
                account.ai_auto_trade = True
            _safe_commit(db)
            _failure_count[login_str] = 0
            logger.info(
                "✅ %s | %s | %s | bal=$%.2f eq=$%.2f%s",
                login_str, account.account_name, account.broker_name,
                account.balance, account.equity,
                " | AI auto-enabled" if was_first_verify else "",
            )
            return True

        # ── BAD SERVER — deterministic, skip immediately ─────────────────────
        if kind == "bad_server":
            account.verification_status = "FAILED"
            account.is_verified = False
            if hasattr(account, "verification_error"):
                account.verification_error = (
                    f'Server "{account.server}" not found. Open your MT5 app, '
                    f"copy the EXACT server name (it must match exactly, e.g. "
                    f"'Exness-MT5Real8'), and re-enter it."
                )
            _safe_commit(db)
            logger.info("⛔ %s FAILED (bad server '%s') — skipping until resubmit",
                        login_str, account.server)
            return False

        # ── BAD AUTH — deterministic, skip immediately ───────────────────────
        if kind == "bad_auth":
            account.verification_status = "FAILED"
            account.is_verified = False
            if hasattr(account, "verification_error"):
                account.verification_error = (
                    "Login or password incorrect. Re-enter your MT5 login number "
                    "and the MASTER (or INVESTOR) password exactly as shown in "
                    "your MT5 app."
                )
            _safe_commit(db)
            logger.info("⛔ %s FAILED (bad login/password) — skipping until resubmit",
                        login_str)
            return False

        # ── IPC WEDGE — this account's server keeps crashing the terminal ─────
        # Fail it fast (2 strikes) so it can't keep taking the whole queue down.
        if kind == "ipc_wedge":
            _failure_count[login_str] += 1
            if _failure_count[login_str] >= 2:
                account.verification_status = "FAILED"
                account.is_verified = False
                if hasattr(account, "verification_error"):
                    account.verification_error = (
                        f'Couldn\'t reach server "{account.server}". It may be '
                        f"offline, or not added in the trading terminal yet. "
                        f"Check the exact server name in your MT5 app "
                        f"(e.g. 'Exness-MT5Real8') and tap Connect to retry."
                    )
                _safe_commit(db)
                logger.warning("⛔ %s FAILED (server '%s' wedged the terminal) — skipping",
                               login_str, account.server)
            else:
                account.verification_status = "PENDING"
                _safe_commit(db)
                logger.info("… %s ipc_wedge on '%s' — strike %d/2",
                            login_str, account.server, _failure_count[login_str])
            return False

        # ── TRANSIENT — retry with backoff, give up after the cap ────────────
        _handle_transient(account, db, login_str, "transient connection issue")
        return False

    except Exception as e:
        logger.error("Verify exception %s: %s", login_str, e)
        _failure_count[login_str] += 1
        try:
            db.rollback()
        except Exception:
            pass
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
      2. PENDING retries (respecting backoff)
      3. VERIFIED accounts whose last_verified_at is stale (periodic refresh)

    FAILED is intentionally EXCLUDED everywhere — a FAILED account has a known
    cause (wrong password / bad server / repeated transient failure) and must
    NOT be retried automatically (that's what stalled the queue). It becomes
    eligible again only when the user RESUBMITS, which sets the status back to
    VERIFYING (handled as a fresh request above).
    """
    now_utc = datetime.now(timezone.utc)
    refresh_cutoff = now_utc - timedelta(seconds=REFRESH_INTERVAL_SEC)

    # 1. HIGHEST PRIORITY — brand-new connect / refresh requests from users.
    fresh_requests = db.query(ClientMT5Account).filter(
        ClientMT5Account.is_active == True,
        or_(
            ClientMT5Account.verification_status == "VERIFYING",
            ClientMT5Account.verification_status == "REFRESH",
        ),
    ).limit(MAX_ACCOUNTS_PER_LOOP).all()

    # 2. Retry PENDING / never-verified (with backoff). FAILED excluded.
    pending = db.query(ClientMT5Account).filter(
        or_(
            ClientMT5Account.verification_status == "PENDING",
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
    # A VERIFYING/REFRESH row means the user JUST submitted (often after fixing
    # wrong details). Reset this login's failure/backoff counters so the
    # corrected credentials are tried IMMEDIATELY with a clean slate.
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
                    processed = 0
                    loop_start = time.time()
                    for account in queue:
                        # Per-loop time budget: defer the rest to the next loop
                        # so fresh VERIFYING/REFRESH requests stay responsive.
                        if time.time() - loop_start > LOOP_TIME_BUDGET_SEC:
                            logger.info("⏳ loop budget reached — deferring %d account(s) to next loop",
                                        len(queue) - processed)
                            break
                        if verify_one_account(account, db):
                            successes += 1
                        processed += 1
                    logger.info("📊 Cycle %d: %d/%d verified", cycle, successes, processed)
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
    logger.info("🚀 MT5 Verifier started | refresh=%ds | batch=%d | login_timeout=%dms | "
               "max_transient=%d | loop_budget=%ds | restart_every=%ds",
               REFRESH_INTERVAL_SEC, MAX_ACCOUNTS_PER_LOOP, LOGIN_TIMEOUT_MS,
               MAX_TRANSIENT_RETRIES, LOOP_TIME_BUDGET_SEC, MT5_RESTART_INTERVAL_SEC)
    main_loop()