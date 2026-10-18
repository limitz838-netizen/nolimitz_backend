"""
================================================================================
  app/ai/day_pnl.py  —  TODAY'S REALISED P&L, READ FROM THE BROKER
================================================================================

  WINDOWS ONLY. This module imports MetaTrader5, so nothing that runs on Render
  may import it. It is imported by mt5_verification_worker.py and by nothing
  else — the same rule the other files in app/ai/ already follow.

  ── WHY THIS EXISTS ──────────────────────────────────────────────────────────

  The dashboard's "Session P&L" was computed in the browser as

      equity_now - equity_when_the_page_loaded

  which is zero whenever no position is open, and resets to zero on every page
  reload. It was never the day's P&L; it was a page-lifetime equity delta.

  The day's real P&L is not in our database at all. live_trades.profit is empty
  (4 rows in the last 30 days, all zero), so there is nothing to sum. The only
  place the truth exists is the broker's own deal history — which the verifier
  is already logged into every five minutes.

  ── WHAT IT RETURNS ──────────────────────────────────────────────────────────

  The same total MT5 itself shows at the bottom of its History tab when the
  range is set to Today: the sum of profit + commission + swap + fee over every
  trade deal of the server's day. Deposits, withdrawals, credit and bonus
  entries are excluded — they move the balance without being P&L.

  That is REALISED only. Floating P&L on still-open positions is
  (equity - balance), which the API adds at read time from the same snapshot.
  Keeping them apart means a value that is wrong is visibly wrong, not blended.

  ── THE SERVER-CLOCK PROBLEM ─────────────────────────────────────────────────

  MT5 timestamps are in the BROKER'S server time, not UTC, and most brokers run
  UTC+2 or UTC+3. Asking for "since UTC midnight" therefore asks the wrong
  question for two or three hours a day — and those are exactly the New York
  afternoon hours these accounts trade.

  The offset is measured from a live tick: a quote's timestamp is server time
  for a moment that is, by definition, now. Over a weekend the last tick is
  days old and that measurement is nonsense, so it is range-checked and the
  last good value is kept. If we have never had a good one, it falls back to
  UTC and says so in the log rather than silently reporting the wrong day.
================================================================================
"""

import logging
import time
from datetime import datetime, timedelta

import MetaTrader5 as mt5

logger = logging.getLogger("verifier")

# Deal types that are real trades. Everything else (2 = BALANCE, 3 = CREDIT,
# 4 = CHARGE, 5 = CORRECTION, 6 = BONUS, ...) moves the balance without being
# trading profit, and must not land in a P&L figure.
_DEAL_TYPE_BUY = 0
_DEAL_TYPE_SELL = 1

# Symbols tried, in order, when measuring the server clock. One of these is on
# essentially every broker's feed.
_CLOCK_SYMBOLS = ("XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD")

# Broker offsets in the real world span UTC-5 to UTC+13. Anything outside this
# is a stale tick, not a time zone.
_MIN_OFFSET_H = -12
_MAX_OFFSET_H = 14

# Last offset we trusted, per terminal process. Weekend ticks are stale, so a
# remembered good value beats re-measuring a dead feed.
_last_good_offset_h: int = None  # type: ignore[assignment]


def server_offset_hours() -> int:
    """Hours to add to UTC to get the broker's clock. 0 if never measurable."""
    global _last_good_offset_h
    for sym in _CLOCK_SYMBOLS:
        try:
            tick = mt5.symbol_info_tick(sym)
        except Exception:
            continue
        if not tick or not getattr(tick, "time", 0):
            continue
        offset = round((tick.time - time.time()) / 3600.0)
        if _MIN_OFFSET_H <= offset <= _MAX_OFFSET_H:
            if offset != _last_good_offset_h:
                logger.info("server clock measured from %s: UTC%+d", sym, offset)
            _last_good_offset_h = offset
            return offset
    if _last_good_offset_h is not None:
        return _last_good_offset_h
    return 0


def server_day_start() -> tuple:
    """(midnight_on_the_server_clock, 'YYYY-MM-DD', offset_hours).

    The datetime is naive on purpose: that is what history_deals_get expects,
    and it reads it as server time.
    """
    offset = server_offset_hours()
    server_now = datetime.utcfromtimestamp(time.time() + offset * 3600)
    midnight = server_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight, midnight.strftime("%Y-%m-%d"), offset


def realised_pnl_today(login: str = "") -> tuple:
    """Today's realised P&L on the CURRENTLY LOGGED-IN account.

    Returns (pnl, day_key, deal_count) or (None, day_key, 0) if the broker
    would not answer. None means "unknown" and must stay distinguishable from
    0.0, which means "traded nothing, or broke even" — a dash and a zero are
    different claims and the user can tell them apart.

    Call this immediately after a successful login, while that account is the
    one the terminal is bound to.
    """
    day_start, day_key, offset = server_day_start()
    # +1 day, not "now": a broker whose clock runs a little ahead of our
    # measurement would otherwise have its newest deals fall outside the range.
    day_end = day_start + timedelta(days=1)

    try:
        deals = mt5.history_deals_get(day_start, day_end)
    except Exception as e:
        logger.warning("day P&L: history_deals_get raised for %s: %s", login, e)
        return None, day_key, 0

    if deals is None:
        # An empty day and a refused request both need saying apart. MT5
        # returns () for "no deals" and None for "could not answer".
        logger.warning("day P&L: no history for %s (%s)", login, mt5.last_error())
        return None, day_key, 0

    total = 0.0
    counted = 0
    for d in deals:
        if getattr(d, "type", -1) not in (_DEAL_TYPE_BUY, _DEAL_TYPE_SELL):
            continue          # deposit, withdrawal, credit, correction, bonus
        total += (float(getattr(d, "profit", 0.0) or 0.0)
                  + float(getattr(d, "commission", 0.0) or 0.0)
                  + float(getattr(d, "swap", 0.0) or 0.0)
                  + float(getattr(d, "fee", 0.0) or 0.0))
        counted += 1

    return round(total, 2), day_key, counted
