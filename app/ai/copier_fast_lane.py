"""
================================================================================
  COPIER FAST LANE  —  master trades reach clients in seconds, not a shard cycle
================================================================================

  THE PROBLEM THIS SOLVES
  Copier execution used to ride inside ai_execution_worker's cycle, which logs
  into every account in turn. Rows were visibly rejected with "stale open event".

  THE INSIGHT
  Copier events are rare and bursty. Almost every second there is nothing to do;
  then one master trade needs many accounts at once. So poll the queue every
  second and log in ONLY to accounts that actually have work.

  ── RUN IT ───────────────────────────────────────────────────────────────────
      set COPIER_LANE_TERMINAL=C:\\MT5\\copier\\terminal64.exe
      py -3.10 -m app.ai.copier_fast_lane

  Give it its OWN terminal. Sharing one with a shard or the verifier causes the
  login failures that look like bad customer credentials.

  ── SHARDING ─────────────────────────────────────────────────────────────────
  One lane is serial: each licence costs a login (~4s), so 45 licences took
  174.6s and the last client entered 141 seconds after the first. That spread —
  not the average — is what clients feel, and it grows linearly with users.

  Run N lanes side by side, each with its OWN terminal directory and its own
  slice of the licences:

      lane 0:  COPIER_SHARD_COUNT=3  COPIER_SHARD_INDEX=0  C:\\MT5\\copier0
      lane 1:  COPIER_SHARD_COUNT=3  COPIER_SHARD_INDEX=1  C:\\MT5\\copier1
      lane 2:  COPIER_SHARD_COUNT=3  COPIER_SHARD_INDEX=2  C:\\MT5\\copier2

  Splitting on license_id %% SHARD_COUNT means no two lanes ever hold the same
  licence, so they cannot fight over a login or double-execute a row.

  Defaults are SHARD_COUNT=1 / SHARD_INDEX=0, which behaves exactly as before.

  NOTE ON BALANCE: the modulo split is uneven in practice. Measured 31 Aug:
  lane 0 held 23 licences, lane 2 held 21, lane 1 held 1. The worst lane sets
  the delivery time, so adding lanes gives less than the arithmetic suggests.

  ── REAPING ──────────────────────────────────────────────────────────────────
  Measured 31 Aug over six hours: 219 rows executed, against 224 rows that cost
  a full MT5 login only to discover there was nothing to do —

        160  "no open trades mapped to this master ticket"
         55  "already closed by client"
          9  "nothing to modify"

  That was roughly 46%% of the cycle spent on logins that could never place a
  trade. None of them needed a terminal: when the master closes a position the
  backend fans a close row out to EVERY licence, but only the licences that
  actually received the OPEN have anything to close — and which ones those are
  is already recorded in the ticket map table.

  reap_unworkable_rows() resolves those rows with one UPDATE at the top of the
  cycle, writing the SAME error_message the executor would have written, so the
  admin activity feed is unchanged. It also clears two long-standing leaks:
  stale opens and opens for expired licences, which used to sit pending forever
  (166 had accumulated over four days) and kept a lane cycling on the same
  licence once per second — one lane's log reached 961,000 lines that way.

  TABLE NAME WARNING: the live ticket map table is `ticket_maps` (2,498 rows on
  31 Aug). There is ALSO a legacy `trade_ticket_maps` table in the same database
  — 0 rows, older schema with is_open / manually_closed / ea_id instead of
  is_closed / closed_by_client. Querying that one silently matches nothing, so
  the table name here is taken from TradeTicketMap.__tablename__ rather than
  hardcoded; if the model ever moves, this follows it instead of breaking.

  ── CYCLE DIAGNOSTICS ────────────────────────────────────────────────────────
  The summary separates login failures, parked accounts, and licences whose
  rows were gone by the time we logged in, so licences that produce nothing are
  visible rather than silently missing from the tally.
================================================================================
"""

import os
import sys
import time
import logging
import subprocess
from typing import Dict, List, Optional

import MetaTrader5 as mt5
from sqlalchemy import func, text

from app.database import SessionLocal
from app.models import ClientMT5Account, TradeExecution, TradeTicketMap
# MAX_OPEN_EVENT_AGE_SEC is imported rather than redeclared so the reaper and
# the executor can never disagree about when an open event is too old to enter.
from app.ai.copier_executor import (
    process_copier_executions,
    MAX_OPEN_EVENT_AGE_SEC,
)

try:
    from app.security_utils import decrypt_secret
except Exception:
    try:
        from app.security import decrypt_text as decrypt_secret
    except Exception:
        def decrypt_secret(v):
            return v


# The real ticket map table, read from the ORM so it can never drift from the
# model — see the TABLE NAME WARNING in the module docstring.
_TICKET_MAP_TABLE = TradeTicketMap.__tablename__


# ==============================================================================
# SHARDING
# ==============================================================================
SHARD_COUNT = max(1, int(os.environ.get("COPIER_SHARD_COUNT", "1")))
SHARD_INDEX = int(os.environ.get("COPIER_SHARD_INDEX", "0")) % SHARD_COUNT
_TAG = "fastlane" if SHARD_COUNT == 1 else f"fastlane{SHARD_INDEX}"

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s | %(levelname)s | {_TAG} | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fastlane")


# ==============================================================================
# CONFIG
# ==============================================================================
TERMINAL_PATH = os.environ.get("COPIER_LANE_TERMINAL", r"C:\MT5\copier\terminal64.exe")
POLL_SEC = float(os.environ.get("COPIER_LANE_POLL_SEC", "1.0"))
INIT_TIMEOUT_MS = int(os.environ.get("MT5_INIT_TIMEOUT_MS", "30000"))
LOGIN_TIMEOUT_MS = int(os.environ.get("MT5_LOGIN_TIMEOUT_MS", "10000"))

# Any licence taking longer than this is named individually, so a single slow
# broker cannot hide inside an average.
SLOW_LICENCE_SEC = float(os.environ.get("COPIER_SLOW_LICENCE_SEC", "8.0"))

# How long a close/modify row must sit before the reaper is willing to resolve
# it without logging in. This is the safety margin for the race where a close
# arrives while its own OPEN is still queued: without the delay we could cancel
# a close for a position that is about to exist, leaving a client holding a
# trade that never closes. Raise it if you ever see that happen.
REAP_MIN_AGE_SEC = int(os.environ.get("COPIER_REAP_MIN_AGE_SEC", "45"))

# Accounts whose login keeps failing are parked so one dead account cannot
# stall the queue for everyone else.
LOGIN_FAIL_LIMIT = int(os.environ.get("COPIER_LANE_FAIL_LIMIT", "3"))
LOGIN_PARK_SEC = int(os.environ.get("COPIER_LANE_PARK_SEC", "900"))

LICENSE_FAIL_LIMIT = 3
LICENSE_PARK_SEC = 300
_license_failures: Dict[int, int] = {}
_license_parked_until: Dict[int, float] = {}

_login_failures: Dict[str, int] = {}
_parked_until: Dict[str, float] = {}
_current_login: Optional[int] = None
_symbol_cache: Dict[str, Optional[str]] = {}

# Per-cycle diagnostics, reset at the top of every cycle.
_diag: Dict[str, int] = {}


def _diag_bump(key: str) -> None:
    _diag[key] = _diag.get(key, 0) + 1


# ==============================================================================
# MT5
# ==============================================================================
def _force_kill_terminal() -> None:
    """A zombied terminal64.exe returns -10005 on every initialize() forever and
    shutdown() cannot revive it. Matched by exact path so this can never touch a
    trader shard's terminal — or another copier lane's."""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process terminal64 -ErrorAction SilentlyContinue | "
             f"Where-Object {{ $_.Path -eq '{TERMINAL_PATH}' }} | Stop-Process -Force"],
            timeout=20, capture_output=True)
        logger.warning("force-killed wedged terminal")
    except Exception as e:
        logger.warning("force-kill failed: %s", e)


def init_terminal() -> bool:
    """Bring the terminal up.

    initialize() returns False when the terminal's SAVED account fails to log
    in — error -6 — even though the terminal itself is running fine. We log in
    per-account anyway, so that is not fatal: what matters is whether
    terminal_info() answers afterwards.
    """
    global _current_login
    _current_login = None

    for attempt in (1, 2):
        try:
            ok = mt5.initialize(path=TERMINAL_PATH, timeout=INIT_TIMEOUT_MS)
        except Exception as e:
            logger.warning("MT5 init exception (attempt %d): %s", attempt, e)
            ok = False

        if not ok:
            err = mt5.last_error()
            if mt5.terminal_info() is not None:
                logger.warning("init reported %s but terminal is up — continuing", err)
                logger.info("terminal up: %s", TERMINAL_PATH)
                return True
            logger.error("MT5 init failed (attempt %d): %s", attempt, err)
            if attempt == 1:
                _force_kill_terminal()
                time.sleep(8)
                continue
            return False

        logger.info("terminal up: %s", TERMINAL_PATH)
        return True

    return False


def ensure_terminal() -> bool:
    if mt5.terminal_info():
        return True
    logger.warning("terminal lost — rebuilding")
    try:
        mt5.shutdown()
    except Exception:
        pass
    time.sleep(2)
    if init_terminal():
        return True
    _force_kill_terminal()
    time.sleep(10)
    return init_terminal()


def _decrypt(value):
    """Passwords on this deployment are plaintext — the Fernet key env var name
    never matched — so a strict decrypt raises InvalidToken on every account.
    Only attempt it on values that actually look like Fernet tokens."""
    if not value:
        return value
    if not str(value).startswith("gAAAAA"):
        return value
    try:
        return decrypt_secret(value)
    except Exception:
        return value


def switch_account(account: ClientMT5Account) -> bool:
    """Log the terminal into this client. Short-circuits when already there, so
    a burst of rows for one account costs a single login."""
    global _current_login
    login = int(account.login)

    if _current_login == login:
        return True

    password = _decrypt(account.password)
    try:
        ok = mt5.login(login, password=password, server=account.server,
                       timeout=LOGIN_TIMEOUT_MS)
    except Exception as e:
        logger.warning("login exception %s: %s", login, e)
        ok = False

    if not ok:
        logger.warning("login failed %s @ %s: %s", login, account.server, mt5.last_error())
        _current_login = None
        return False

    _current_login = login
    return True


def resolve_symbol(base: str) -> Optional[str]:
    """Fallback broker-symbol lookup.

    copier_executor now resolves against the logged-in terminal's own symbol
    list and only consults this as a hint, so the guesswork here no longer
    decides anything on its own.
    """
    key = (base or "").upper()
    if key in _symbol_cache:
        return _symbol_cache[key]

    candidates = [key]
    if "XAU" in key or key == "GOLD":
        candidates += ["XAUUSD", "XAUUSDM", "XAUUSDC", "GOLD", "GOLDM"]
    elif "BTC" in key:
        candidates += ["BTCUSD", "BTCUSDM", "BTCUSDT"]
    else:
        candidates += [key + "M", key + "C", key + "."]

    resolved = None
    try:
        symbols = mt5.symbols_get() or []
        names = {s.name.upper(): s.name for s in symbols}
        for c in candidates:
            if c in names:
                resolved = names[c]
                break
        if resolved is None:
            for upper, real in names.items():
                if key and key in upper:
                    resolved = real
                    break
    except Exception as e:
        logger.debug("symbol resolve failed for %s: %s", base, e)

    if resolved:
        _symbol_cache[key] = resolved
    return resolved


# ==============================================================================
# REAPER  —  resolve rows that need no MT5 login
# ==============================================================================
def reap_unworkable_rows(db) -> Dict[str, int]:
    """Resolve pending rows whose outcome is already knowable from the database.

    See the REAPING section of the module docstring for the measurements that
    motivated this. Each statement writes the SAME error_message the executor
    would have written after logging in, so nothing about the admin activity
    feed changes — only the ~4s login is avoided.

    Runs on every lane. The statements are idempotent and each lane's rows are
    a disjoint slice, so overlap is harmless.

    Returns a small tally, or {} when there was nothing to do.
    """
    out: Dict[str, int] = {}

    # ---- 1. Opens that are already too old to enter ------------------------
    # _handle_open would skip these as stale after paying for the login.
    # Resolving them here also stops them accumulating forever.
        # SHARDED ON PURPOSE. Only this lane may declare its own rows too old.
    #
    # Without the modulo, all seven lanes reap all seven slices, so a row this
    # lane is about to work on can be killed by another lane first. That is a
    # race the slow brokers always lose: licence 271 on a live server was
    # reaped 11 times out of 11 and never once executed, while demo-broker
    # licences on the same shard sailed through because their logins were
    # quick enough to beat the other lanes' reapers.
    #
    # Statements 2 and 3 below stay global. Their answers do not depend on
    # which lane is asking — an expired licence is expired for everyone — so
    # letting any lane resolve them is a saving, not a race.
    #
    # mod(license_id, 1) = 0 is always true, so this is a no-op when unsharded.
    r = db.execute(text("""
        update trade_executions
           set status = 'skipped',
               error_message = 'stale open event — expired before execution'
         where status = 'pending'
           and event_type = 'open'
           and created_at < now() - make_interval(secs => :age)
           and mod(license_id, :shard_count) = :shard_index
    """), {"age": MAX_OPEN_EVENT_AGE_SEC,
           "shard_count": SHARD_COUNT, "shard_index": SHARD_INDEX})
    if r.rowcount:
        out["stale_opens"] = r.rowcount

    # ---- 2. Opens for licences that cannot trade ---------------------------
    # The executor's licence gate deliberately lets CLOSE and MODIFY through
    # for a lapsed licence, so nobody is left holding a position they cannot
    # exit. This mirrors that exactly: opens only, never closes.
    r = db.execute(text("""
        update trade_executions te
           set status = 'skipped',
               error_message = 'licence expired — entries paused'
          from licenses l
         where l.id = te.license_id
           and te.status = 'pending'
           and te.event_type = 'open'
           and (l.is_active = false or l.expires_at <= now())
    """))
    if r.rowcount:
        out["expired_licence"] = r.rowcount

    # ---- 3. Closes and modifies with nothing to act on ---------------------
    # THE BIG ONE — the 160-row bucket. A close row for a licence with no open
    # ticket map can only ever produce "no open trades mapped to this master
    # ticket", which is exactly what _handle_close returns.
    #
    # Two guards against the race where a close arrives before its own open has
    # been executed:
    #   • the row must be at least REAP_MIN_AGE_SEC old
    #   • no pending or processing OPEN row may exist for the same master
    #     ticket on the same licence
    # If either guard is uncertain the row is left alone and takes the slow
    # path, because wrongly cancelling a close would strand a client in a
    # position the master has already exited.
    r = db.execute(text(f"""
        update trade_executions te
           set status = 'skipped',
               error_message = 'no open trades mapped to this master ticket'
         where te.status = 'pending'
           and te.event_type in ('close', 'modify')
           and te.created_at < now() - make_interval(secs => :age)
           and not exists (
                 select 1 from {_TICKET_MAP_TABLE} m
                  where m.license_id = te.license_id
                    and m.master_ticket = te.master_ticket
                    and m.is_closed = false)
           and not exists (
                 select 1 from trade_executions o
                  where o.license_id = te.license_id
                    and o.master_ticket = te.master_ticket
                    and o.event_type = 'open'
                    and o.status in ('pending', 'processing'))
    """), {"age": REAP_MIN_AGE_SEC})
    if r.rowcount:
        out["nothing_to_close"] = r.rowcount

    if out:
        db.commit()
    return out


# ==============================================================================
# QUEUE
# ==============================================================================
def pending_license_ids(db) -> List[int]:
    """Which licences have work waiting, restricted to this lane's slice.

    The modulo runs in the database, so each lane's poll stays one cheap indexed
    query and no lane ever sees another lane's licences.
    """
    q = db.query(TradeExecution.license_id).filter(
        TradeExecution.status == "pending")

    if SHARD_COUNT > 1:
        q = q.filter(TradeExecution.license_id % SHARD_COUNT == SHARD_INDEX)

    rows = (q.group_by(TradeExecution.license_id)
             .order_by(func.min(TradeExecution.id).asc())
             .all())
    return [r[0] for r in rows if r[0] is not None]


def _is_parked(login: str) -> bool:
    until = _parked_until.get(login, 0.0)
    if until and time.time() < until:
        return True
    if until:
        _parked_until.pop(login, None)
        _login_failures.pop(login, None)
    return False


def _note_login_failure(login: str) -> None:
    _login_failures[login] = _login_failures.get(login, 0) + 1
    if _login_failures[login] >= LOGIN_FAIL_LIMIT:
        _parked_until[login] = time.time() + LOGIN_PARK_SEC
        logger.warning("parking %s for %ds after %d login failures",
                       login, LOGIN_PARK_SEC, _login_failures[login])


def _note_license_failure(license_id: int) -> None:
    """Park a licence that keeps raising, so one bad row cannot spin the loop
    at one attempt per second and starve every other client."""
    _license_failures[license_id] = _license_failures.get(license_id, 0) + 1
    if _license_failures[license_id] >= LICENSE_FAIL_LIMIT:
        _license_parked_until[license_id] = time.time() + LICENSE_PARK_SEC
        logger.warning("parking licence %s for %ds after %d failures",
                       license_id, LICENSE_PARK_SEC, _license_failures[license_id])


def handle_license(db, license_id: int) -> Dict[str, int]:
    account = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_id,
        ClientMT5Account.is_active == True,      # noqa: E712
        ClientMT5Account.is_verified == True,    # noqa: E712
    ).first()

    if not account:
        # No usable account: fail the rows rather than leave them pending
        # forever, so the queue never grows without bound.
        rows = db.query(TradeExecution).filter(
            TradeExecution.license_id == license_id,
            TradeExecution.status == "pending",
        ).all()
        for row in rows:
            row.status = "failed"
            row.error_message = "no verified active MT5 account for this licence"
        db.commit()
        _diag_bump("no_account")
        return {"failed": len(rows)}

    login = str(account.login)
    if _is_parked(login):
        _diag_bump("parked")
        return {}

    if not switch_account(account):
        _note_login_failure(login)
        _diag_bump("login_failed")
        return {}

    _login_failures.pop(login, None)
    result = process_copier_executions(db, account, resolve_symbol) or {}

    # Rows were pending when we polled but gone once we logged in — either the
    # reaper resolved them between the poll and the login, or another worker
    # claimed them. Worth counting: it is a login spent for nothing.
    if not result:
        _diag_bump("rows_taken")

    return result


# ==============================================================================
# LOOP
# ==============================================================================
def main() -> None:
    logger.info("COPIER FAST LANE | terminal=%s | poll=%.1fs | shard %d/%d | maps=%s",
                TERMINAL_PATH, POLL_SEC, SHARD_INDEX, SHARD_COUNT, _TICKET_MAP_TABLE)

    if not os.path.exists(TERMINAL_PATH):
        logger.critical("terminal not found at %s — set COPIER_LANE_TERMINAL", TERMINAL_PATH)
        sys.exit(1)

    if not init_terminal():
        logger.critical("could not start terminal")
        sys.exit(1)

    idle_since = time.time()

    while True:
        started = time.time()
        db = SessionLocal()
        try:
            # Resolve everything that needs no terminal BEFORE asking which
            # licences have work. Reaping first is the whole point: those rows
            # never reach license_ids, so they never cost a login.
            try:
                reaped = reap_unworkable_rows(db)
                if reaped:
                    logger.info("reaped %s", reaped)
            except Exception as e:
                # A reaper fault must never stop trades being delivered.
                logger.warning("reap failed: %s", e)
                try:
                    db.rollback()
                except Exception:
                    pass

            license_ids = pending_license_ids(db)

            if license_ids:
                if not ensure_terminal():
                    logger.error("terminal unrecoverable — retrying shortly")
                    time.sleep(5)
                    continue

                _diag.clear()
                totals: Dict[str, int] = {}
                slowest: List[str] = []

                for license_id in license_ids:
                    if time.time() < _license_parked_until.get(license_id, 0):
                        _diag_bump("licence_parked")
                        continue

                    lic_started = time.time()
                    try:
                        for status, count in handle_license(db, license_id).items():
                            totals[status] = totals.get(status, 0) + count
                    except Exception as e:
                        logger.error("licence %s failed: %r", license_id, e, exc_info=True)
                        _note_license_failure(license_id)
                        _diag_bump("raised")
                        try:
                            db.rollback()
                        except Exception:
                            pass

                    lic_elapsed = time.time() - lic_started
                    if lic_elapsed >= SLOW_LICENCE_SEC:
                        slowest.append(f"{license_id}:{lic_elapsed:.0f}s")

                elapsed = time.time() - started
                if totals or _diag:
                    per = elapsed / max(1, len(license_ids))
                    extra = f" | {_diag}" if _diag else ""
                    logger.info("%d licence(s) in %.1fs (%.1fs each) → %s%s",
                                len(license_ids), elapsed, per, totals or {}, extra)
                    if slowest:
                        logger.info("slow licences: %s", ", ".join(slowest))
                idle_since = time.time()

            elif time.time() - idle_since > 300:
                # Quiet proof-of-life every 5 minutes so silence is not mistaken
                # for a dead worker.
                logger.info("idle — no pending copier rows")
                idle_since = time.time()

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error("loop error: %s", e)
        finally:
            try:
                db.close()
            except Exception:
                pass

        elapsed = time.time() - started
        time.sleep(max(0.2, POLL_SEC - elapsed))

    logger.info("shutting down")
    mt5.shutdown()


if __name__ == "__main__":
    main()
