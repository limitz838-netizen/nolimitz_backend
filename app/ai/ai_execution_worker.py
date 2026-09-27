"""
================================================================================
  NOLIMITZ AI — EXECUTION WORKER (THE TRADER)
================================================================================

  ROLE
  ----
  Trusts the watcher (THE BRAIN) for trade direction & confidence.
  Owns:  execution quality | account protection | timing | slippage |
         retries | lot sizing | broker compatibility | trade management |
         exits | partial closes | trailing | account-specific behavior

  This worker does NOT analyze markets. The watcher decides what & when.
  This worker decides HOW to execute and HOW to manage.

  Designed to scale cleanly from 100 -> 1000+ users.

  ── FIX PACK (this revision) ─────────────────────────────────────────────────
  1. RECOVERY FLIPS ARE NOW PER-LOGIN. They were keyed by position.magic
     (777777 for everyone) and injected into the SHARED signal dict — one
     user's cut loss re-entered a trade on EVERY account. Now keyed by
     (login, symbol_class) and injected only into that user's own signal set.
  2. POSITION STATE SURVIVES RESTARTS. Scale-out stage and runner peak are
     persisted on LiveTrade (scale_stage, peak_profit_001) every cycle and
     rebuilt at startup — a worker restart no longer re-fires SCALE1 on a
     runner or forgets its peak.
  3. ONE SL MODIFY PER CYCLE. Four overlapping lock blocks (PROFIT_LOCK,
     BREAKEVEN_PROTECT, BE_LOCK_$1, LOCK_PROFIT_$2) are consolidated into a
     single unified lock: compute the tightest stop all rules agree on, send
     at most one SLTP order. Legacy tier ladder now runs ONLY when scale-out
     is disabled.
  4. NO MORE $0 CLAMP. Reconciliation records MT5's confirmed realized profit
     as-is (close-deals-only + cent scaling already guarantee sanity). The old
     clamp wrote real losses to history as "$0 BREAKEVEN" and poisoned the
     learning loop.
  5. MANUAL TRADES ALWAYS EXECUTE. The old early-return on "no fresh signals"
     silently skipped pending "Send to MT5" requests unless an unrelated
     signal happened to exist.
  6. USER PANIC BUTTON. close_all_requested on the account closes every open
     position immediately and stops the AI.
  7. HEARTBEAT TO DB. The dashboard can now show "engine online / last cycle
     Xs ago" via WorkerHeartbeat instead of going silently dark.
  8. CREDENTIALS DECRYPTED AT USE. Passwords are encrypted at rest by the API
     (NOLIMITZ_CRED_KEY); this worker decrypts just-in-time, with plaintext
     fallback for legacy rows.

  ARCHITECTURE
  ------------
  Cycle (every LOOP_DELAY seconds):
    1. Health-check MT5 terminal
    2. Pull latest fresh AISignal (the brain's decision)
    3. Refresh learning stats from AITradeHistory (every 30 min)
    4. For each ai_auto_trade=True account (up to MAX_USERS_PER_CYCLE):
        a. Switch to that account's MT5 login
        b. Account protection checks (margin, equity, daily loss)
        c. Manage existing positions (3-tier exits + reversal + stale)
        d. Reconcile closed trades -> write to AITradeHistory (learning loop)
        e. If signal is fresh & user has symbol enabled:
             - Validate execution feasibility (spread, margin, lot)
             - Send order with adaptive stops + filling + retry
             - Persist trade record (resilient)
================================================================================
"""

import os
import time
import signal as sys_signal
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Dict, List, Tuple

import MetaTrader5 as mt5
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    AISignal, ClientMT5Account, ClientSymbolSetting,
    LiveTrade, AITradeExecution, License, ManualTradeRequest,
    WorkerHeartbeat,
)
from app.ai.models.ai_trade_history import AITradeHistory
from app.ai.copier_executor import process_copier_executions

# Credential decryption — passwords are encrypted at rest by the API when
# NOLIMITZ_CRED_KEY is set. decrypt_secret() transparently returns plaintext
# rows unchanged, so rollout is safe with a mixed DB.
try:
    from app.security_utils import decrypt_secret
except Exception:
    def decrypt_secret(v):
        return v


# ==============================================================================
# LOGGING
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("trader")


# ==============================================================================
# CONFIG
# ==============================================================================
class Config:
    """All worker behavior tuned here. Production-safe defaults."""

    ORDER_COMMENT = "Nolimitz Ai"
    MAGIC = 777777

    # ── GLOBAL AI ENTRY GATE ────────────────────────────────────────────────
    # Master switch for AI-initiated NEW entries, across ALL accounts.
    # Set env AI_ENTRIES_ENABLED=false to stop the AI opening any new trades
    # while EVERYTHING else keeps running: position management, exits,
    # reconciliation, close-all, heartbeats, and user-initiated MANUAL trades.
    # Reversible instantly — flip the env var and restart the worker.
    # Rationale: five entry strategies were tested through backtest + walk-
    # forward validation (Jul 2026) and none showed a real edge; until a
    # configuration passes validation AND a demo forward-test, every AI entry
    # has measured negative expectancy.
    AI_ENTRIES_ENABLED = (os.environ.get("AI_ENTRIES_ENABLED", "true")
                          .lower() == "true")

    # ── TWO-TRACK OPERATION (client worker + demo worker, same DB) ──────────
    # AI_ONLY_LOGINS:  comma-separated MT5 logins — this worker processes ONLY
    #                  these accounts (use on the DEMO worker).
    # AI_SKIP_LOGINS:  comma-separated logins this worker must NEVER touch
    #                  (use on the CLIENT worker to exclude the demo accounts).
    # WORKER_NAME:     heartbeat identity override so /engine-status shows the
    #                  two workers separately (default: trader-shard{N}).
    # Both filters empty = process everything (single-worker behavior).
    AI_ONLY_LOGINS = {s.strip() for s in
                      os.environ.get("AI_ONLY_LOGINS", "").split(",") if s.strip()}
    AI_SKIP_LOGINS = {s.strip() for s in
                      os.environ.get("AI_SKIP_LOGINS", "").split(",") if s.strip()}
    WORKER_NAME = os.environ.get("WORKER_NAME", "")

    # ── Signal trust (worker trusts brain's confidence) ─────────────────────
    MIN_CONFIDENCE_STANDARD = 55
    MIN_CONFIDENCE_PRIORITY = 50
    SIGNAL_MAX_AGE_SECONDS  = 240
    PRIORITY_KEYWORDS = ("XAU", "GOLD", "BTC", "BITCOIN")

    # ── Lot sizing ──────────────────────────────────────────────────────────
    # THE USER'S PER-SYMBOL LOT IS THE ONLY LOT SOURCE (see compute_lot).
    # The old MODE_LOTS table and LOT_SOURCE_PRIORITY switch were dead code —
    # no path used them — and their comments contradicted the live behavior,
    # so they have been REMOVED. What remains below are pure safety bounds.
    LOT_MIN = 0.01

    # Safety cap per asset class — refuses to size beyond this regardless of mode
    LOT_CLASS_MAX = {
        "BTC": 2.0, "ETH": 2.0, "GOLD": 1.0,
        "INDEX": 5.0, "OIL": 5.0, "FOREX": 2.0, "JPY": 2.0, "OTHER": 1.0,
    }

    # Safety FLOOR per asset class — broker minimum (0.01 everywhere).
    LOT_CLASS_MIN = {
        "BTC":   0.01,
        "ETH":   0.01,
        "GOLD":  0.01,
        "INDEX": 0.01,
        "OIL":   0.01,
        "FOREX": 0.01,
        "JPY":   0.01,
        "OTHER": 0.01,
    }

    # Per-mode max trades (per symbol). SINGLE SOURCE OF TRUTH:
    #   normal = 1, medium = 3, aggressive = 5. (Older comments claiming
    #   2/3/4 were stale and have been removed.)
    RISK_MODE = {
        "normal":     {"max_trades": 1},
        "medium":     {"max_trades": 3},
        "aggressive": {"max_trades": 5},
    }
    DEFAULT_MODE = "medium"
    DEFAULT_MAX_TRADES = 2

    # ── 5-PIP SCALP TARGET (USD per 0.01 lot) ───────────────────────────────
    # Each new trade gets a tight TP at "5 pips" effective, expressed as a
    # USD target per 0.01 lot. Worker computes the price distance from the
    # broker's actual tick value so this works on every broker symbol suffix.
    # Scales linearly with lot — e.g. 0.10 BTC has target = $5 x 10 = $50.
    SCALP_TARGET_USD_PER_001 = {
        "GOLD":  0.50,    # 0.50 price move = $0.50 on 0.01 lot
        "BTC":   5.00,    # 50 price move = $5 on 0.01 lot
        "ETH":   2.50,
        "INDEX": 5.00,
        "OIL":   5.00,
        "FOREX": 0.50,
        "JPY":   0.50,
        "OTHER": 1.00,
    }
    # ── RISK:REWARD (professional sizing) ───────────────────────────────────
    # OLD behavior risked 3x the target (R:R 1:3 AGAINST us) — one loss wiped
    # ~3 wins. A professional does the OPPOSITE: the stop is TIGHTER than the
    # first target so reward >= risk. We set SL = 0.7x the TP distance, giving
    # a base R:R of ~1.4:1 in our favour BEFORE the scale-out runner (which
    # pushes realized R:R higher by letting winners run).
    SCALP_SL_MULTIPLIER = 0.7   # SL distance = 0.7x TP distance -> R:R ~1.4:1 FOR us
    # Hard ceiling on stop distance as % of price, so a volatile BTC reading
    # can never place a $400 stop on a small account. Keeps risk bounded.
    MAX_SL_PCT_OF_PRICE = 0.004  # stop never more than 0.4% of price away
    # Max price drift (in ATR units) from the signal price before we skip a
    # user this cycle. Keeps every user's entry comparable — a user reached
    # late in the cycle, after price already ran 1+ ATR the signal's way, is
    # skipped rather than given a chase entry. 1.0 ATR is a sane default.
    MAX_ENTRY_SLIPPAGE_ATR = float(os.environ.get("MAX_ENTRY_SLIPPAGE_ATR", "1.0"))
    # ── Gold strategy mode ──────────────────────────────────────────────────
    # GOLD_TREND_SCALP: trade WITH gold's continuous M5 trend and scalp the run
    # (momentum-ignition + pullback continuation), entering fast — gold tends to
    # run in sustained directional moves. This REPLACES the mean-reversion fade
    # (which sold into uptrends). Set False to fall back to the fade below.
    GOLD_TREND_SCALP   = (os.environ.get("GOLD_TREND_SCALP", "true").lower() == "true")
    GOLD_IGNITION_ATR  = float(os.environ.get("GOLD_IGNITION_ATR", "0.5"))  # M1 body / ATR to "ignite"
    # ── Mean-reversion fade for XAU (legacy) — now OFF by default because it
    # fought the trend (sold rallies). Kept as a toggle for comparison.
    MEAN_REVERSION_XAU = (os.environ.get("MEAN_REVERSION_XAU", "false").lower() == "true")
    MEANREV_PUSH_ATR   = float(os.environ.get("MEANREV_PUSH_ATR", "1.5"))  # push size to fade
    MEANREV_TP_ATR     = float(os.environ.get("MEANREV_TP_ATR", "0.8"))    # reversion target
    MEANREV_SL_ATR     = float(os.environ.get("MEANREV_SL_ATR", "1.2"))    # stop beyond push
    # Gold TREND-scalp bracket — wider than the scalp path so a normal pullback
    # inside a trend doesn't wick the stop. SL ~1 ATR of room; TP ~1.6 ATR (the
    # scale-out / profit-lock bank most of the move long before TP is reached).
    GOLD_TREND_SL_ATR  = float(os.environ.get("GOLD_TREND_SL_ATR", "1.0"))
    GOLD_TREND_TP_ATR  = float(os.environ.get("GOLD_TREND_TP_ATR", "1.6"))
    # Only fade a push that runs AGAINST the M5 trend. With this on, an up-push
    # is sold only when M5 is NOT trending up (and a down-push bought only when
    # M5 is NOT trending down) — so the bot stops selling into a clean uptrend.
    MEANREV_TREND_FILTER = (os.environ.get("MEANREV_TREND_FILTER", "true").lower() == "true")
    # When an opposite signal arrives while a position is open, we CLOSE and
    # flip (catch the reversal). This cooldown stops flip-flop churn — at most
    # one flip per symbol per this many seconds.
    REVERSAL_FLIP_COOLDOWN_SEC = int(os.environ.get("REVERSAL_FLIP_COOLDOWN_SEC", "120"))
    # Spread-quality gate: don't trade when the spread is too large relative to
    # ATR (costs eat the edge). Backtest across 2023-2026 showed every year
    # profitable at <=10%. This is the "trade at the right time" filter — a
    # disciplined trader sits out when conditions are unfavourable.
    MAX_SPREAD_ATR_RATIO = float(os.environ.get("MAX_SPREAD_ATR_RATIO", "0.10"))
                                 # but BE lock pulls SL to entry quickly
    # Adaptive widening: keep spread <= this fraction of the TP. If a fixed
    # 5-pip TP would make spread larger than this, widen the TP. This lets
    # the bot trade in wider-spread conditions by demanding a bigger move.
    SCALP_TARGET_SPREAD_PCT = 0.30   # spread should be <=30% of TP
    SCALP_MAX_WIDEN_MULT    = 4.0    # never widen TP beyond 4x the base target

    # ── Account protection ──────────────────────────────────────────────────
    MIN_MARGIN_LEVEL_PCT  = 200.0   # don't trade if margin level drops below
    MIN_FREE_MARGIN_PCT   = 20.0    # need at least 20% free margin
    DAILY_LOSS_PCT_LIMIT  = 0.0     # 0 = disabled; set 5.0 for 5% cap
    EQUITY_DRAWDOWN_PCT   = 0.0     # 0 = disabled; from peak equity
    NO_MONEY_BACKOFF_SEC  = 300
    # Quarantine an account after this many consecutive login failures, for this
    # long — stops a dead/bad account re-wedging the terminal every cycle.
    LOGIN_QUARANTINE_AFTER = int(os.environ.get("LOGIN_QUARANTINE_AFTER", "3"))
    LOGIN_QUARANTINE_SEC   = int(os.environ.get("LOGIN_QUARANTINE_SEC", "1800"))

    # ── Execution quality ───────────────────────────────────────────────────
    MAX_DEVIATION_POINTS  = 30
    MAX_SPREAD_MULT_VS_AVG = 3.0    # skip if current spread > 3x recent avg
    ORDER_RETRY_LIMIT     = 3
    RETRY_BACKOFF_SEC     = 0.2

    # ── Adaptive stops (broker compatibility) ───────────────────────────────
    ATR_SL_MIN_MULT  = 2.5
    STOP_ESCALATION  = [1.0, 1.5, 2.5, 4.0]
    FALLBACK_MIN_PIPS = {
        "GOLD": 30, "BTC": 1500, "ETH": 100,
        "FOREX": 10, "JPY": 12, "INDEX": 15, "OIL": 25, "OTHER": 15,
    }

    # ── Timing ──────────────────────────────────────────────────────────────
    LOOP_DELAY              = int(os.environ.get("LOOP_DELAY", "3"))
    # Every N cycles, process EVERY account (no fast-path skipping) so idle
    # users still get orphan-position recovery and balance sync. At a 3s loop,
    # 20 ~= once a minute.
    FULL_SWEEP_EVERY        = int(os.environ.get("FULL_SWEEP_EVERY", "20"))
    MAX_USERS_PER_CYCLE     = int(os.environ.get("MAX_USERS_PER_CYCLE", "100"))
    MAX_OPENS_PER_USER_PER_CYCLE = int(os.environ.get("MAX_OPENS_PER_USER", "4"))
    # ── Horizontal sharding (run N worker PROCESSES in parallel) ────────────
    # Each process owns its OWN MT5 terminal (set MT5_TERMINAL_PATH per process)
    # and handles a DISJOINT slice of users -> trades placed in PARALLEL instead
    # of one terminal switching accounts one-by-one. Shard by LOGIN so every row
    # of an account stays on one shard (never double-traded).
    SHARD_INDEX = int(os.environ.get("SHARD_INDEX", "0"))
    SHARD_TOTAL = int(os.environ.get("SHARD_TOTAL", "1"))
    FLIP_COOLDOWN_PRIORITY  = 3
    FLIP_COOLDOWN_STANDARD  = 8

    # ── Exit risk management (lot-scaled — 0.01 lot = $1 unit) ──────────────
    # NOTE: the values below are consumed by the UNIFIED profit lock and the
    # LEGACY tier ladder (which only runs when SCALE_OUT_ENABLED is False).
    EXIT_BE_LOCK_USD      = 2.0    # +$2 -> move SL to lock profit
    EXIT_BE_BUFFER_USD    = 0.5    # (legacy ladder) SL goes to entry + $0.50
    EXIT_PARTIAL1_USD     = 2.0    # (legacy) +$2 -> close 25%
    EXIT_PARTIAL1_PCT     = 0.25
    EXIT_PROFIT_LOCK_USD  = 3.5    # (legacy) +$3.5 -> tighten SL to lock $2
    EXIT_PROFIT_LOCK_BUF  = 2.0
    EXIT_PARTIAL2_USD     = 5.0    # (legacy) +$5 -> close another 25%
    EXIT_PARTIAL2_PCT     = 0.25
    EXIT_TRAIL_START_USD  = 7.0    # (legacy) +$7 -> activate trailing
    EXIT_TRAIL_MIN_LOCK   = 5.0    # (legacy) trail SL never locks less than $5
    TRAIL_STEP_STRONG     = 3.0    # trail $3 behind price on strong trends
    TRAIL_STEP_MODERATE   = 2.0
    TRAIL_STEP_WEAK       = 1.2

    # ── Basket TP: close ALL trades on a symbol when total profit hits target ──
    # When multiple trades are open on the same symbol, manage them as a basket.
    # Once combined profit hits BASKET_TP_PER_001_LOT x (total basket lot / 0.01),
    # close everything. Pure scalper discipline: take the win, look for next setup.
    BASKET_TP_ENABLED        = True
    BASKET_TP_PER_001_LOT    = 1.5   # $1.50 per 0.01 lot of total basket size
    BASKET_TP_MIN_POSITIONS  = 2     # only kicks in with 2+ positions

    # ── SCALPER BRAIN (pre-entry confirmation by the worker itself) ─────────
    # The watcher provides signals at 60% confidence. The worker is now a
    # SECOND BRAIN that confirms or rejects each signal based on live price
    # action — the way a 10-year-experience scalper would decide whether
    # to take the entry NOW or pass on it.
    # NOTE: thresholds tuned from production logs. Too strict and nothing
    # opens; too loose and we take garbage. These are the sweet-spot values.
    BRAIN_ENABLED                = True   # master switch
    BRAIN_MIN_CONFIDENCE         = 60     # brain's job is price-action
                                           # confirmation, not re-checking
                                           # confidence. 60 lets the watcher's
                                           # good signals through.
    BRAIN_MAX_SPREAD_PCT_OF_TP   = 0.62   # spread up to 62% of TP is tradeable
    BRAIN_MAX_CHASE_ATR_MULT     = 2.5    # a bit more room before "chasing"
    BRAIN_MIN_ATR_RATIO          = 0.20   # allow quieter markets (was 0.30)
    BRAIN_MAX_ATR_RATIO          = 5.0    # allow more volatility (was 4.0)
    BRAIN_REQUIRE_MOMENTUM       = True   # still confirm, but softer (below)
    BRAIN_MOMENTUM_M1_LOOKBACK   = 3      # M1 candles to check
    BRAIN_MOMENTUM_M1_MIN_AGREE  = 1      # only need 1 of 3 same-direction
                                           # (was 2) — don't fight the signal,
                                           # just avoid entering dead against it
    BRAIN_SIGNAL_MAX_AGE_SEC     = 120    # signals up to 2 min old still ok
    BRAIN_REJECTION_LOOKBACK     = 15     # fewer bars -> fewer false rejections
    BRAIN_REJECTION_PROXIMITY    = 0.15   # tighter "near" zone
    BRAIN_PRICE_SANITY_PCT       = 0.05   # refuse if signal price >5% off
                                           # (catches watcher BTCJPY-in-BTCUSD)

    # ── Adaptive hold (let runners run, scalp everything else) ──────────────
    # SCALE-OUT MODEL (the user's BTC observation made into the core strategy):
    # Instead of fully closing every winner at +$2, we SCALE OUT — take partial
    # profit, lock the rest at breakeven, and let the runner ride a trailing
    # stop. This banks something on every winner (consistency) while letting
    # the occasional big move pay for the losers (asymmetry = the real edge).
    SCALE_OUT_ENABLED            = True
    # Stage 1: at +$2/0.01lot, take 50% off and move SL to breakeven.
    SCALE1_TRIGGER_USD           = 2.0
    SCALE1_CLOSE_PCT             = 0.50
    # Stage 2: at +$4/0.01lot, take another 25% and lock SL at +$2.
    SCALE2_TRIGGER_USD           = 4.0
    SCALE2_CLOSE_PCT             = 0.25
    SCALE2_LOCK_USD              = 2.0    # SL locks +$2/0.01lot of profit
    # Runner: the final 25% trails behind price and only exits on the trailing
    # stop or a confirmed reversal — this is what catches the big moves.
    RUNNER_TRAIL_USD             = 2.0    # trail the runner $2/0.01lot behind peak
    RUNNER_REVERSAL_CLOSE        = True   # close runner on confirmed reversal candle

    # ── PROFIT LOCK — once a trade is up at least TRIGGER in real money, pull
    # the SL to lock (profit - GIVEBACK), floored at MIN_LOCK so it is always
    # at least a small profit. The stop only ratchets UP. Consumed by the
    # UNIFIED lock in manage_position (one SL modify per cycle).
    PROFIT_PROTECT_TRIGGER_USD  = float(os.environ.get("PROFIT_PROTECT_TRIGGER_USD", "5.0"))
    PROFIT_PROTECT_GIVEBACK_USD = float(os.environ.get("PROFIT_PROTECT_GIVEBACK_USD", "3.0"))
    PROFIT_PROTECT_MIN_LOCK_USD = float(os.environ.get("PROFIT_PROTECT_MIN_LOCK_USD", "1.0"))

    # LEGACY hard-close (kept as a fallback ONLY if SCALE_OUT_ENABLED is False)
    SCALP_CLOSE_PROFIT_USD       = 2.0
    BE_LOCK_TRIGGER_USD          = 2.0    # at +$2 floating, move SL to entry
    HOLD_RUNNER_NEAR_TP_PCT      = 0.95   # decide hold/release when 95% to TP
    HOLD_RUNNER_BODY_VS_ATR      = 1.5    # last candle body > 1.5x ATR to extend
    HOLD_RUNNER_EXTENDS          = 1      # max times we'll extend TP per trade
    HOLD_RUNNER_REVERSAL_CLOSE   = True   # in profit + confirmed reversal -> close to bank it

    # ── Anti-blind-entry gates ──────────────────────────────────────────────
    # Don't pile into losing setups; pause symbols showing consecutive losses
    RECENT_LOSS_COOLDOWN_SEC      = 180   # don't re-enter same symbol for 3min after loss
    CONSECUTIVE_LOSS_LIMIT        = 3     # 3 losses in window -> pause symbol
    CONSECUTIVE_LOSS_WINDOW_SEC   = 1800  # 30 min window
    CONSECUTIVE_LOSS_PAUSE_SEC    = 900   # 15 min pause when triggered
    EXISTING_LOSER_MAX_DD_USD     = 3.0   # don't pile on same-direction position
                                          # already down more than $3/0.01lot

    # ── HARD CAPS (capital protection — cannot be overridden) ──────────────
    # Same-dir cap: absolute ceiling of same-direction positions per symbol
    # class. Set to 5 so the aggressive mode (5 trades) is honoured; normal (1)
    # and medium (3) sit safely under it. The mode's max_trades is the
    # operational number, this just prevents anything beyond 5.
    HARD_MAX_SAME_DIR_PER_SYMBOL  = {
        "BTC":   5,
        "ETH":   5,
        "GOLD":  5,
        "OIL":   5,
        "INDEX": 5,
        "JPY":   5,
        "FOREX": 5,
        "OTHER": 3,
    }
    HARD_MAX_TOTAL_POSITIONS      = 20       # ceiling across ALL symbols
    HARD_MAX_NOTIONAL_PER_BALANCE = 60.0     # max (notional / balance) ratio
    # Max % of balance used as MARGIN for a single new position. Caps lot size
    # to what the account can actually afford so a small account trades small
    # (e.g. on $207, BTC sizes to ~0.01 instead of 0.65). The single most
    # important safety setting — prevents no-money rejections and over-leverage.
    MARGIN_BUDGET_PCT = float(os.environ.get("MARGIN_BUDGET_PCT", "0.20"))  # 20%

    # ── Smart exits (emergency) ─────────────────────────────────────────────
    REVERSAL_EXIT_PROFIT_MAX = 3.0
    STALE_MINUTES            = 20
    STALE_USD_BAND           = 0.5   # per 0.01 lot

    # ── Learning ────────────────────────────────────────────────────────────
    LEARNING_REFRESH_SEC     = 1800
    LEARNING_LOOKBACK_DAYS   = 30
    LEARNING_MIN_TRADES      = 8
    LEARNING_BOOST_WR        = 0.65
    LEARNING_REDUCE_WR       = 0.40
    LEARNING_BOOST_FACTOR    = 1.3
    LEARNING_REDUCE_FACTOR   = 0.7

    # ── Master MT5 (optional, env-driven) ──────────────────────────────────
    MASTER_LOGIN    = int(os.environ.get("MT5_MASTER_LOGIN", "0"))
    MASTER_PASSWORD = os.environ.get("MT5_MASTER_PASSWORD", "")
    MASTER_SERVER   = os.environ.get("MT5_MASTER_SERVER", "")

cfg = Config()


# ==============================================================================
# GLOBAL STATE (carefully shared)
# ==============================================================================
MT5_LOCK    = threading.Lock()
USER_LOCKS  : Dict[int, bool]    = defaultdict(bool)
_shutdown   = False
_current_login: Optional[int]    = None

# ── CYCLE-SCOPED MARKET-DATA CACHE ───────────────────────────────────────────
# The single biggest cost of holding MT5_LOCK per user is repeated broker
# round-trips for SLOW-MOVING market data: every calculate_atr()/read_market()
# call is a copy_rates_from_pos() over the wire. During a signal burst, user 1
# and user 40 both trade XAUUSD — and both independently re-fetch the identical
# M15 ATR and market read, dozens of redundant calls per cycle.
#
# These derived values (ATR, trend/momentum read off M15/M5 CLOSED candles) do
# NOT change meaningfully within one ~3s cycle. So we memoize them for a short
# window: the FIRST user to need XAUUSD's ATR pays the round-trip; every
# subsequent user in the same window gets it from memory instantly. This
# collapses management-time broker calls and directly shrinks lock-held time.
#
# SAFETY — what is and isn't cached:
#   • CACHED: calculate_atr, read_market — pure (symbol→value), read-only,
#     derived from CLOSED candles, identical across users within a cycle.
#   • NEVER CACHED: symbol_info_tick / live bid-ask, positions_get,
#     account_info — these are per-account or must be live for entry pricing
#     and money decisions. They are untouched.
# Keyed by (symbol, period, kind). TTL is deliberately shorter than one loop.
_MKT_CACHE_TTL_SEC = float(os.environ.get("MKT_CACHE_TTL_SEC", "2.0"))
_mkt_cache: Dict[tuple, tuple] = {}          # key -> (expires_at, value)
_mkt_cache_lock = threading.Lock()

def _mkt_cache_get(key: tuple):
    """Return cached value if still fresh, else None. Thread-safe."""
    now = time.time()
    with _mkt_cache_lock:
        hit = _mkt_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    return None

def _mkt_cache_put(key: tuple, value) -> None:
    with _mkt_cache_lock:
        _mkt_cache[key] = (time.time() + _MKT_CACHE_TTL_SEC, value)

def _mkt_cache_sweep() -> None:
    """Drop expired entries so the dict can't grow unbounded. Called once per
    cycle from main_loop (cheap — a handful of symbols)."""
    now = time.time()
    with _mkt_cache_lock:
        for k in [k for k, (exp, _) in _mkt_cache.items() if exp <= now]:
            _mkt_cache.pop(k, None)

# Per-symbol broker quirks (learned and cached)
_symbol_min_stop_distance: Dict[str, float] = {}
_symbol_filling_mode    : Dict[str, int]    = {}
_symbol_spread_history  : Dict[str, list]   = defaultdict(list)

# Per-account state
_no_money_until         : Dict[int, float]  = {}
# Bad accounts (dead trial servers / wrong creds) whose login keeps failing —
# quarantine them so one can't wedge the terminal or waste the cycle every loop.
_login_fail_count       : Dict[int, int]    = defaultdict(int)
_login_quarantine_until : Dict[int, float]  = {}
_daily_loss             : Dict[tuple, float] = defaultdict(float)  # (login, date)
_equity_peak            : Dict[int, float]  = {}
_last_action_time       : Dict[tuple, float] = defaultdict(float)  # (login, symbol)
_last_flip_time         : Dict[tuple, float] = defaultdict(float)  # (login, sym_class) reversal flips

# Anti-blind-entry tracking: list of (timestamp, profit) per (login, symbol)
# Used for consecutive-loss circuit breaker and recent-loss cooldown
_recent_outcomes        : Dict[tuple, list] = defaultdict(list)
# When a symbol is paused due to consecutive losses, store unlock time
_symbol_paused_until    : Dict[tuple, float] = defaultdict(float)  # (login, symbol)

# Per-position state — PERSISTED to LiveTrade.scale_stage / peak_profit_001
# every cycle and REBUILT at startup (_rebuild_position_state), so a worker
# restart never re-fires a partial or forgets a runner's peak.
_partial_closed_tickets : set = set()
_partial_closed_tier2   : set = set()
_runner_extended_tickets: set = set()    # tickets whose TP was extended (runner)

# Scale-out state (winner-exit model)
_scale1_done: set = set()                # tickets that took the stage-1 partial
_scale2_done: set = set()                # tickets that took the stage-2 partial
_runner_peak: dict = {}                  # ticket -> peak profit-per-0.01lot seen

# Learning
_symbol_winrate         : Dict[str, float] = {}
_symbol_trades          : Dict[str, int]   = {}
_setup_winrate          : Dict[str, float] = {}
_setup_trades           : Dict[str, int]   = {}
_learning_last_refresh  : float = 0
# Highest AISignal ID at worker startup. Used as fallback when created_at is NULL —
# any signal with ID > this is considered "new since I started" and tradeable.
_startup_high_water_mark: int = 0


# ==============================================================================
# GRACEFUL SHUTDOWN
# ==============================================================================
def _handle_shutdown(signum, frame):
    global _shutdown
    logger.info("🛑 Shutdown — finishing cycle cleanly")
    _shutdown = True

sys_signal.signal(sys_signal.SIGINT, _handle_shutdown)
sys_signal.signal(sys_signal.SIGTERM, _handle_shutdown)


# ==============================================================================
# HEARTBEAT — lets the API/dashboard show "engine online, last cycle Xs ago"
# ==============================================================================
def _beat(db: Session, name: str, detail: str = "") -> None:
    """Upsert this worker's heartbeat row. Best-effort — never raises."""
    try:
        row = db.query(WorkerHeartbeat).filter_by(worker_name=name).first()
        if not row:
            row = WorkerHeartbeat(worker_name=name)
            db.add(row)
        row.detail = (detail or "")[:200]
        row.last_beat = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


# ==============================================================================
# MT5 LIFECYCLE
# ==============================================================================
def init_mt5() -> bool:
    term_path = os.environ.get(
        "MT5_TERMINAL_PATH",
        r"C:\Users\Administrator\Desktop\TraderMT5\terminal64.exe",
    )
    portable = os.environ.get("MT5_PORTABLE", "false").lower() == "true"
    if not mt5.initialize(path=term_path, portable=portable):
        logger.critical("MT5 init failed (path=%s): %s", term_path, mt5.last_error())
        return False

    term = mt5.terminal_info()

    if term:
        logger.info(
            "Trader MT5 connected: path=%s",
            term.path
        )

    if cfg.MASTER_LOGIN and cfg.MASTER_PASSWORD and cfg.MASTER_SERVER:
        if mt5.login(cfg.MASTER_LOGIN, cfg.MASTER_PASSWORD, cfg.MASTER_SERVER):
            logger.info("✅ Master MT5 connected")
        else:
            logger.warning("Master login failed (continuing per-user): %s", mt5.last_error())
    else:
        logger.info("✅ MT5 initialized (per-user login mode)")
    return True


def switch_account(login: int, password: str, server: str) -> bool:
    """Switch terminal to user's account, verifying the switch."""
    global _current_login
    if _current_login == login:
        info = mt5.account_info()
        if info and info.login == login:
            return True
    if not mt5.login(login, password, server):
        _current_login = None
        return False
    info = mt5.account_info()
    if not info or info.login != login:
        _current_login = None
        return False
    _current_login = login
    return True


def _force_kill_terminal() -> None:
    """Force-kill ONLY our terminal, matched by exact path so it never touches
    another MT5 install (e.g. the verifier's) on the same machine. A hung
    terminal64.exe returns -10005 'IPC timeout' on every initialize() forever —
    shutdown()+initialize() can't revive a zombied process; it must be killed
    and relaunched fresh."""
    term_path = os.environ.get(
        "MT5_TERMINAL_PATH", r"C:\Users\user\Desktop\TraderMT5\terminal64.exe")
    try:
        import subprocess
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process terminal64 -ErrorAction SilentlyContinue | "
             f"Where-Object {{ $_.Path -eq '{term_path}' }} | Stop-Process -Force"],
            timeout=20, capture_output=True,
        )
        logger.warning("🔪 force-killed wedged terminal at %s", term_path)
    except Exception as e:
        logger.warning("force-kill failed: %s", e)


def _recover_terminal(max_soft: int = 2) -> bool:
    """Bring MT5 back. Soft reconnects first; if those keep timing out (zombied
    terminal), force-kill the process and relaunch it — PATIENTLY, in one pass.

    A cold terminal64.exe needs 20-40s before its IPC pipe answers. The old
    code killed, waited 5s, tried init ONCE, then let the next cycle run — which
    force-killed the half-launched terminal all over again, looping for an hour.
    Here we kill once, clear the broken Python IPC handle with shutdown(), then
    retry init several times with long waits so the terminal finishes starting
    up before anything can kill it again."""
    for attempt in range(1, max_soft + 1):
        try:
            mt5.shutdown()
        except Exception:
            pass
        time.sleep(3)
        if init_mt5():
            return True
        logger.warning("soft reconnect %d/%d failed: %s",
                       attempt, max_soft, mt5.last_error())

    _force_kill_terminal()
    # Clear the Python side's broken IPC handle BEFORE relaunch — re-initializing
    # on top of a dead handle is itself a cause of the -10005 timeout loop.
    try:
        mt5.shutdown()
    except Exception:
        pass

    # Patient cold-start: up to ~6 attempts x 15s = ~90s, all in THIS pass.
    for attempt in range(1, 7):
        time.sleep(15)
        if init_mt5():
            logger.info("✅ terminal recovered after force-kill (attempt %d)", attempt)
            return True
        logger.warning("post-kill init %d/6 failed: %s", attempt, mt5.last_error())

    logger.error("terminal still down after force-kill — retry next cycle")
    return False


# ==============================================================================
# SYMBOL UTILITIES (broker compatibility)
# ==============================================================================
def classify_symbol(symbol: str) -> str:
    s = symbol.upper()
    if "XAU" in s or "GOLD" in s:    return "GOLD"
    if "BTC" in s or "BITCOIN" in s: return "BTC"
    if "ETH" in s or "ETHEREUM" in s: return "ETH"
    if "JPY" in s and any(p in s for p in ["USD","EUR","GBP","AUD","NZD","CAD","CHF"]):
        return "JPY"
    if any(idx in s for idx in ["US30","US500","NAS100","SPX","DAX","FTSE","NDX","DOW"]):
        return "INDEX"
    if any(oil in s for oil in ["OIL","WTI","BRENT","USOIL","UKOIL"]):
        return "OIL"
    if any(fx in s for fx in ["EUR","GBP","USD","AUD","NZD","CAD","CHF"]):
        return "FOREX"
    return "OTHER"


def is_priority(symbol: str) -> bool:
    s = symbol.upper()
    return any(kw in s for kw in cfg.PRIORITY_KEYWORDS)


def find_broker_symbol(base: str) -> Optional[str]:
    """Multi-suffix resolution: works on any broker."""
    b = base.upper().replace(" ", "")
    candidates = [b]
    for suffix in (".A", ".M", ".RAW", ".ECN", ".PRO", ".CASH", "M", "C", "+"):
        if b.endswith(suffix):
            candidates.append(b[:-len(suffix)])
    if "BTC" in b:
        candidates.extend(["BTCUSD", "BTC", "BITCOIN", "BTCUSDT"])
    if "XAU" in b:
        candidates.extend(["XAUUSD", "GOLD"])
    all_symbols = mt5.symbols_get() or []
    for c in candidates:
        for s in all_symbols:
            if s.name.upper().replace(" ", "") == c:
                return s.name
    for s in all_symbols:
        n = s.name.upper().replace(" ", "")
        for c in candidates:
            if c and c in n and len(c) >= 3:
                return s.name
    return None


def get_pip_size(info) -> float:
    if not info: return 0.0001
    return 10 ** -(info.digits - 1) if info.digits in (3, 5) else 10 ** -info.digits


def get_spread_pips(broker_sym: str) -> float:
    tick = mt5.symbol_info_tick(broker_sym)
    info = mt5.symbol_info(broker_sym)
    if not tick or not info: return 999.0
    raw = tick.ask - tick.bid
    pip = get_pip_size(info)
    return round(raw / pip, 2) if pip > 0 else 999.0


def _record_spread_sample(broker_sym: str, spread: float) -> None:
    """Track recent spreads to detect anomalous widening."""
    hist = _symbol_spread_history[broker_sym]
    hist.append(spread)
    if len(hist) > 30:
        hist.pop(0)


def spread_is_acceptable(broker_sym: str) -> Tuple[bool, float, float]:
    """
    Returns (ok, current_spread, avg_spread).
    Skips trade only if current spread is wildly above recent average.
    """
    current = get_spread_pips(broker_sym)
    hist = _symbol_spread_history[broker_sym]
    if not hist:
        _record_spread_sample(broker_sym, current)
        return True, current, current
    avg = sum(hist) / len(hist)
    _record_spread_sample(broker_sym, current)
    if avg > 0 and current > avg * cfg.MAX_SPREAD_MULT_VS_AVG:
        return False, current, avg
    return True, current, avg


def _calculate_atr_uncached(broker_sym: str, period: int = 14) -> float:
    try:
        rates = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M15, 0, period + 5)
        if rates is None or len(rates) < period + 1: return 0.0
        trs = []
        for i in range(-period, 0):
            h = rates[i]["high"]; l = rates[i]["low"]
            prev_c = rates[i - 1]["close"]
            trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
        return sum(trs) / period
    except Exception:
        return 0.0


def calculate_atr(broker_sym: str, period: int = 14) -> float:
    """Cycle-cached ATR. ATR is derived from CLOSED M15 candles, so it's the
    same for every user within a short window — fetch it once, reuse it. Falls
    straight through to the raw fetch on a miss (see _mkt_cache above)."""
    key = ("atr", broker_sym, period)
    cached = _mkt_cache_get(key)
    if cached is not None:
        return cached
    val = _calculate_atr_uncached(broker_sym, period)
    # Only cache a real reading — never poison the cache with a 0.0 failure,
    # so a momentary fetch glitch can't suppress ATR for the whole window.
    if val and val > 0:
        _mkt_cache_put(key, val)
    return val


# ==============================================================================
# USER SETTINGS (source of truth)
# ==============================================================================
def parse_max_trades(setting, risk_mode: str) -> int:
    """
    Returns max simultaneous trades per symbol — driven by the RISK MODE:
        normal     -> 1 trade  (safest)
        medium     -> 3 trades
        aggressive -> 5 trades

    The user picks the mode; the mode sets how many positions can stack per
    symbol. Lot size is separate (the user's per-pair choice). Still bounded
    by the per-class HARD_MAX_SAME_DIR_PER_SYMBOL ceiling as a final safety.
    """
    mode = cfg.RISK_MODE.get(risk_mode, cfg.RISK_MODE[cfg.DEFAULT_MODE])
    return mode.get("max_trades", cfg.DEFAULT_MAX_TRADES)


def parse_user_lot(setting) -> Optional[float]:
    """
    Returns the user's configured lot for this symbol, or None if not set.
    None means the worker should NOT trade this symbol (explicit lot required).
    """
    try:
        v = float(setting.lot_size) if setting.lot_size else 0
        if v > 0:
            return v
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def parse_direction(setting) -> str:
    try:
        d = (setting.trade_direction or "both").lower().strip()
        if d in ("buy", "sell", "both"): return d
    except AttributeError: pass
    return "both"


def direction_allows(direction: str, action: str) -> bool:
    if direction == "both": return True
    if direction == "buy"  and action == "BUY":  return True
    if direction == "sell" and action == "SELL": return True
    return False


# ==============================================================================
# LOT SIZING PIPELINE
# ==============================================================================
def normalize_to_broker(broker_sym: str, lot: float, sym_class: str) -> float:
    """Apply per-class floor, broker's volume_step/min/max, and class safety cap."""
    cap   = cfg.LOT_CLASS_MAX.get(sym_class, 1.0)
    floor = cfg.LOT_CLASS_MIN.get(sym_class, cfg.LOT_MIN)
    # Enforce class floor BEFORE broker normalization
    lot = max(lot, floor)
    lot = min(lot, cap)
    info = mt5.symbol_info(broker_sym)
    if info:
        step = info.volume_step or 0.01
        min_vol = info.volume_min or 0.01
        max_vol = min(info.volume_max or cap, cap)
        lot = round(lot / step) * step
        # Broker min must still be respected, but our floor takes priority if higher
        lot = max(min_vol, floor, min(max_vol, lot))
    return max(cfg.LOT_MIN, round(lot, 2))


def compute_lot(broker_sym: str, user_lot: Optional[float], risk_mode: str,
               learning_mult: float = 1.0,
               account_balance: float = 1000.0) -> Tuple[float, str]:
    """
    Lot sizing — THE USER IS ALWAYS IN CONTROL.

    The worker uses EXACTLY the per-symbol lot the user set in their settings
    (e.g. XAUUSD 0.02, BTCUSD 0.20). There is NO mode lot, NO learning
    multiplier, NO environment override that can change this. This is a hard
    safety rule: a small account must never be traded at a size the user did
    not choose. (The earlier bug — a 0.20 setting trading at 0.50 — came from
    a mode lot overriding the user's value. That path is now removed entirely.)

    Only if the user enabled a symbol but left the lot blank do we fall back to
    the broker minimum (never a large mode lot), so a missing value can never
    produce an oversized trade.

    The balance/margin cap below still applies as a pure backstop against a
    typo (e.g. user types 50 instead of 0.50), but it can only ever make the
    lot SMALLER, never larger than what the user set.
    """
    sym_class = classify_symbol(broker_sym)

    if user_lot is not None and user_lot > 0:
        raw = user_lot
        source = "user_exact"
    else:
        # User enabled the symbol but typed no lot. Do NOT use the broker's
        # volume_min blindly — on some brokers BTC's minimum is 0.5, which
        # would put a huge position on a small account. Use the smallest the
        # broker allows, but the balance cap below will shrink it to what the
        # account can actually afford.
        info0 = mt5.symbol_info(broker_sym)
        vmin = (info0.volume_min if info0 and info0.volume_min else 0.01)
        raw = vmin
        source = "user_blank_min"

    final_lot = normalize_to_broker(broker_sym, raw, sym_class)

    # ── BALANCE-AWARE CAP ───────────────────────────────────────────────────
    # Cap the lot so the position's REQUIRED MARGIN uses at most
    # MARGIN_BUDGET_PCT of the balance. This makes sizing scale with the
    # account automatically: small accounts trade small, big accounts trade big.
    try:
        if account_balance and account_balance > 0:
            margin_per_lot = mt5.order_calc_margin(
                mt5.ORDER_TYPE_BUY, broker_sym,
                1.0, mt5.symbol_info_tick(broker_sym).ask
            )
            if margin_per_lot and margin_per_lot > 0:
                budget = account_balance * cfg.MARGIN_BUDGET_PCT
                max_affordable = budget / margin_per_lot
                info_b = mt5.symbol_info(broker_sym)
                vmin = (info_b.volume_min if info_b else 0.01) or 0.01
                if final_lot > max_affordable:
                    capped = normalize_to_broker(broker_sym, max_affordable, sym_class)
                    if capped < vmin:
                        # Even the broker's MINIMUM lot needs more margin than
                        # the budget allows. Forcing vmin here is exactly what
                        # put a 0.5 BTC trade on a small account. Instead, refuse:
                        # this symbol is too big for this account right now.
                        return 0.0, f"{source}+unaffordable"
                    final_lot = max(vmin, capped)
                    source = f"{source}+balcap"
    except Exception as e:
        logger.debug("balance cap failed for %s: %s", broker_sym, e)

    return final_lot, source


# ==============================================================================
# ADAPTIVE STOPS (handles broker minimum stop distance)
# ==============================================================================
def min_stop_distance(broker_sym: str) -> float:
    # Broker's REQUIRED minimum only — NOT strategy padding. The old version
    # forced max(2.5xATR, widest-stop-ever-used), which clamped every
    # breakeven / profit-lock move back out so the stop never reached entry.
    # Gone: honour only stops_level + a small per-class fallback. send_order's
    # STOP_ESCALATION widens on the rare broker rejection.
    info = mt5.symbol_info(broker_sym)
    if not info: return 0.0
    point = info.point or 0.00001
    sym_class = classify_symbol(broker_sym)
    broker_min = (info.trade_stops_level or 0) * point
    fb_pips    = cfg.FALLBACK_MIN_PIPS.get(sym_class, 15)
    fb_min     = fb_pips * get_pip_size(info)
    return max(broker_min, fb_min)


def build_valid_stops(broker_sym: str, action: str,
                     desired_sl: Optional[float], desired_tp: Optional[float],
                     escalation_mult: float = 1.0) -> Tuple[float, float, bool]:
    info = mt5.symbol_info(broker_sym)
    tick = mt5.symbol_info_tick(broker_sym)
    if not info or not tick: return 0, 0, False
    digits = info.digits
    min_dist = min_stop_distance(broker_sym) * escalation_mult
    current = tick.ask if action == "BUY" else tick.bid
    if action == "BUY":
        max_sl = current - min_dist; min_tp = current + min_dist
        sl = desired_sl if (desired_sl is not None and desired_sl < max_sl) else max_sl
        tp = desired_tp if (desired_tp is not None and desired_tp > min_tp) else min_tp
    else:
        min_sl = current + min_dist; max_tp = current - min_dist
        sl = desired_sl if (desired_sl is not None and desired_sl > min_sl) else min_sl
        tp = desired_tp if (desired_tp is not None and desired_tp < max_tp) else max_tp
    sl = round(sl, digits); tp = round(tp, digits)
    if action == "BUY"  and (sl >= current or tp <= current): return sl, tp, False
    if action == "SELL" and (sl <= current or tp >= current): return sl, tp, False
    return sl, tp, True


# ==============================================================================
# FILLING MODE (handles broker fill type quirks)
# ==============================================================================
def get_filling_mode(broker_sym: str) -> int:
    if broker_sym in _symbol_filling_mode:
        return _symbol_filling_mode[broker_sym]
    info = mt5.symbol_info(broker_sym)
    if not info: return mt5.ORDER_FILLING_IOC
    flags = info.filling_mode
    if flags & 1:   mode = mt5.ORDER_FILLING_FOK
    elif flags & 2: mode = mt5.ORDER_FILLING_IOC
    else:           mode = mt5.ORDER_FILLING_RETURN
    _symbol_filling_mode[broker_sym] = mode
    return mode


# ==============================================================================
# ORDER SEND (retry + slippage + adaptive stops + filling rotation)
# ==============================================================================
# MT5 trade retcodes by NUMERIC VALUE (stable across all MT5 versions).
# Newer MetaTrader5 builds dropped some TRADE_RETCODE_* attribute names, which
# crashed the worker on import. Use getattr with the known numeric fallback so
# it works on every version.
def _rc(name: str, value: int) -> int:
    return getattr(mt5, name, value)

RC_DONE          = _rc("TRADE_RETCODE_DONE", 10009)
RC_REQUOTE       = _rc("TRADE_RETCODE_REQUOTE", 10004)
RC_PRICE_CHANGED = _rc("TRADE_RETCODE_PRICE_CHANGED", 10020)
RC_PRICE_OFF     = _rc("TRADE_RETCODE_PRICE_OFF", 10021)
RC_CONNECTION    = _rc("TRADE_RETCODE_CONNECTION", 10031)
RC_TIMEOUT       = _rc("TRADE_RETCODE_TIMEOUT", 10012)

_RETRY_CODES = {
    RC_REQUOTE, RC_PRICE_CHANGED,
    RC_PRICE_OFF, RC_CONNECTION,
    RC_TIMEOUT,
}


def send_order(broker_sym: str, action: str, lot: float,
              desired_sl: Optional[float], desired_tp: Optional[float],
              comment: Optional[str] = None):
    """
    Professional order send:
      - Adaptive stops escalation (handles 10016)
      - Filling mode rotation (handles 10030)
      - Retry with backoff on transient errors
      - Validates filled price within slippage tolerance
    Returns (mt5_result, used_sl, used_tp).
    """
    info = mt5.symbol_info(broker_sym)
    if not info: return None, None, None
    order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
    filling = get_filling_mode(broker_sym)
    comment = comment or cfg.ORDER_COMMENT
    last_res = None; used_sl = None; used_tp = None

    for esc_mult in cfg.STOP_ESCALATION:
        tick = mt5.symbol_info_tick(broker_sym)
        if not tick: time.sleep(0.1); continue
        price = tick.ask if action == "BUY" else tick.bid
        sl, tp, valid = build_valid_stops(broker_sym, action, desired_sl, desired_tp, esc_mult)
        if not valid: continue
        used_sl, used_tp = sl, tp

        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": broker_sym,
            "volume": lot, "type": order_type, "price": price,
            "sl": sl, "tp": tp,
            "deviation": cfg.MAX_DEVIATION_POINTS,
            "magic": cfg.MAGIC, "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling,
        }

        for retry in range(cfg.ORDER_RETRY_LIMIT):
            res = mt5.order_send(request)
            last_res = res
            if res is None:
                time.sleep(cfg.RETRY_BACKOFF_SEC * (retry + 1)); continue

            if res.retcode == RC_DONE:
                # Cache learned minimum stop distance for this broker symbol
                stop_used = abs(price - sl)
                cached = _symbol_min_stop_distance.get(broker_sym, 0.0)
                if stop_used > cached:
                    _symbol_min_stop_distance[broker_sym] = stop_used
                # ── VERIFY THE STOP LOSS ACTUALLY GOT SET ──────────────────
                # A trade that fills WITHOUT a stop can run unchecked (one XAU
                # trade ran 29 points to -$294). Some brokers silently drop the
                # SL if it's too close. Confirm the open position has a non-zero
                # SL; if not, force it on via a modify. No trade runs naked.
                if sl and sl > 0:
                    try:
                        time.sleep(0.05)
                        opened = [p for p in (mt5.positions_get(symbol=broker_sym) or [])
                                  if p.ticket == res.order or p.magic == cfg.MAGIC]
                        for p in opened:
                            if p.ticket == res.order and (not p.sl or p.sl == 0.0):
                                mt5.order_send({
                                    "action": mt5.TRADE_ACTION_SLTP,
                                    "symbol": broker_sym, "position": p.ticket,
                                    "sl": sl, "tp": tp,
                                })
                                logger.info("🛡️ SL re-applied on %s ticket %s (broker dropped it)",
                                           broker_sym, p.ticket)
                    except Exception as e:
                        logger.debug("post-open SL verify failed: %s", e)
                return res, sl, tp

            if res.retcode == 10030:  # invalid/unsupported fill mode
                # Try EVERY filling mode right now (don't burn retries one at a
                # time). Many brokers only accept one specific mode; rotating
                # through all three here reliably finds it. Whichever works gets
                # cached so future orders on this symbol use it directly.
                resolved = False
                for fmode in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC,
                              mt5.ORDER_FILLING_RETURN):
                    if fmode == filling:
                        continue
                    request["type_filling"] = fmode
                    res2 = mt5.order_send(request)
                    last_res = res2
                    if res2 and res2.retcode == RC_DONE:
                        _symbol_filling_mode[broker_sym] = fmode
                        stop_used = abs(price - sl)
                        if stop_used > _symbol_min_stop_distance.get(broker_sym, 0.0):
                            _symbol_min_stop_distance[broker_sym] = stop_used
                        return res2, sl, tp
                    if res2 and res2.retcode != 10030:
                        # A different error — stop rotating, handle below
                        res = res2
                        break
                if res.retcode == 10030:
                    # No filling mode worked — give up on this symbol's order
                    break

            if res.retcode == 10016:  # invalid stops -> escalate
                break

            if res.retcode in _RETRY_CODES:
                time.sleep(cfg.RETRY_BACKOFF_SEC * (retry + 1))
                continue

            return res, sl, tp

    return last_res, used_sl, used_tp


# ==============================================================================
# TRADE MANAGEMENT — position scanning & exits
# ==============================================================================
# Recovery-flip store: when we cut a bad entry because price moved aggressively
# AGAINST it, we record the symbol + the trend direction to re-enter WITH.
#
# ★ FIXED: keyed by (login, symbol_class) — previously keyed by position.magic,
# which is 777777 for EVERY position on the platform, so one user's cut loss
# armed a re-entry that fired on EVERY account. Now a flip belongs to exactly
# one login, and process_user injects it into that user's own signal set only.
# {(login:int, symbol_class:str): {"dir": "BUY"/"SELL", "ts": time, "symbol": broker_sym}}
_recovery_flips: dict = {}

def _flag_recovery_flip(login: Optional[int], position, mk: dict,
                        force_dir: str = None):
    """Record that, after closing this bad entry, THIS login wants to re-enter
    WITH the trend that beat it. Direction = opposite of the closed losing
    position, OR the explicit aggressive direction. The actual re-entry still
    goes through the full user-settings pipeline next cycle."""
    try:
        if login is None:
            return
        is_buy = (position.type == mt5.POSITION_TYPE_BUY)
        flip_dir = force_dir or ("SELL" if is_buy else "BUY")
        # Only flip if the market read agrees with the flip direction (don't
        # blindly reverse into chop — go WITH the confirmed aggressive move).
        bias_ok = ((flip_dir == "BUY" and mk.get("bias") != "DOWN") or
                   (flip_dir == "SELL" and mk.get("bias") != "UP"))
        if not bias_ok:
            return
        sym_cls = classify_symbol(position.symbol)
        _recovery_flips[(login, sym_cls)] = {
            "dir": flip_dir, "ts": time.time(), "symbol": position.symbol,
        }
        logger.info("🔄 RECOVERY FLIP armed (login=%s): %s -> re-enter %s with the trend",
                   login, position.symbol, flip_dir)
    except Exception as e:
        logger.debug("flag recovery flip failed: %s", e)


def _close_position(position, reason: str) -> str:
    tick = mt5.symbol_info_tick(position.symbol)
    if not tick: return "HOLD"
    close_price = tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask
    filling = get_filling_mode(position.symbol)
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": position.symbol,
        "volume": position.volume,
        "type": mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
        "position": position.ticket, "price": close_price,
        "deviation": cfg.MAX_DEVIATION_POINTS, "magic": cfg.MAGIC,
        "comment": f"{cfg.ORDER_COMMENT} - {reason}",
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling,
    }
    res = mt5.order_send(req)
    if res and res.retcode == 10030:
        for alt in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
            if alt == filling: continue
            req["type_filling"] = alt
            res = mt5.order_send(req)
            if res and res.retcode == RC_DONE:
                _symbol_filling_mode[position.symbol] = alt
                break
    if res and res.retcode == RC_DONE:
        logger.info("🎯 %s %s | profit=$%.2f | ticket=%d",
                   reason, position.symbol, position.profit, position.ticket)
        _partial_closed_tickets.discard(position.ticket)
        _partial_closed_tier2.discard(position.ticket)
        _runner_extended_tickets.discard(position.ticket)
        _scale1_done.discard(position.ticket)
        _scale2_done.discard(position.ticket)
        _runner_peak.pop(position.ticket, None)
        return reason
    return "HOLD"


def _partial_close(position, pct: float, label: str) -> str:
    info = mt5.symbol_info(position.symbol)
    if not info: return "HOLD"
    step = info.volume_step or 0.01
    vol = round((position.volume * pct) / step) * step
    vol = max(info.volume_min or 0.01, vol)
    vol = round(vol, 2)
    if (position.volume - vol) < (info.volume_min or 0.01):
        return _close_position(position, "FULL_FROM_PARTIAL")
    tick = mt5.symbol_info_tick(position.symbol)
    if not tick: return "HOLD"
    close_price = tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask
    filling = get_filling_mode(position.symbol)
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": position.symbol,
        "volume": vol,
        "type": mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
        "position": position.ticket, "price": close_price,
        "deviation": cfg.MAX_DEVIATION_POINTS, "magic": cfg.MAGIC,
        "comment": f"{cfg.ORDER_COMMENT} - {label}",
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling,
    }
    res = mt5.order_send(req)
    if res and res.retcode == RC_DONE:
        logger.info("💰 %s %s | -%.2f | profit=$%.2f",
                   label, position.symbol, vol, position.profit)
        return "PARTIAL_CLOSED"
    return "HOLD"


def _sl_is_better_generic(position, new_sl: float, is_buy: bool) -> bool:
    """True if new_sl is a tighter (more protective) stop than the current one."""
    cur = position.sl
    if cur is None or cur == 0:
        return True
    return new_sl > cur if is_buy else new_sl < cur


def _set_sl(position, new_sl: float, label: str) -> bool:
    info = mt5.symbol_info(position.symbol)
    if not info: return False
    tick = mt5.symbol_info_tick(position.symbol)
    if tick:
        min_dist = min_stop_distance(position.symbol)
        current = tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask
        if position.type == mt5.POSITION_TYPE_BUY and new_sl >= current - min_dist:
            new_sl = current - min_dist
        elif position.type == mt5.POSITION_TYPE_SELL and new_sl <= current + min_dist:
            new_sl = current + min_dist
    req = {
        "action": mt5.TRADE_ACTION_SLTP, "symbol": position.symbol,
        "position": position.ticket, "sl": round(new_sl, info.digits),
        "tp": position.tp, "deviation": 20,
    }
    res = mt5.order_send(req)
    if res and res.retcode == RC_DONE:
        logger.info("🔒 %s %s | sl=%.5f | profit=$%.2f",
                   label, position.symbol, new_sl, position.profit)
        return True
    return False


def _set_tp(position, new_tp: float, label: str) -> bool:
    """Modify TP on an open position (used to extend TP for runner moves)."""
    info = mt5.symbol_info(position.symbol)
    if not info:
        return False
    tick = mt5.symbol_info_tick(position.symbol)
    if tick:
        min_dist = min_stop_distance(position.symbol)
        current = tick.ask if position.type == mt5.POSITION_TYPE_BUY else tick.bid
        if position.type == mt5.POSITION_TYPE_BUY and new_tp <= current + min_dist:
            new_tp = current + min_dist
        elif position.type == mt5.POSITION_TYPE_SELL and new_tp >= current - min_dist:
            new_tp = current - min_dist
    req = {
        "action": mt5.TRADE_ACTION_SLTP, "symbol": position.symbol,
        "position": position.ticket, "sl": position.sl,
        "tp": round(new_tp, info.digits), "deviation": 20,
    }
    res = mt5.order_send(req)
    if res and res.retcode == RC_DONE:
        logger.info("🎯 %s %s | new_tp=%.5f", label, position.symbol, new_tp)
        return True
    return False


def _m1_strength(symbol: str) -> str:
    """Light M1 strength gauge — for trail step sizing only."""
    try:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 15)
        if rates is None or len(rates) < 15: return "WEAK"
        closes = [r["close"] for r in rates]
        move = abs(closes[-1] - closes[-5])
        avg = sum(abs(closes[i] - closes[i-1]) for i in range(1, 15)) / 14
        if avg <= 0: return "WEAK"
        if move > avg * 2.1: return "STRONG"
        if move > avg:       return "MODERATE"
        return "WEAK"
    except Exception:
        return "MODERATE"


def detect_aggressive_move(symbol: str) -> str:
    """
    Detects aggressive directional moves on M1.
    Three conditions must ALL match for a 'strong' signal:
      1. Last 3 M1 candles same direction (all bullish or all bearish)
      2. Combined body size > 2x avg body of last 15 candles
      3. Net move > 1.5x M15 ATR(14)

    Returns "AGGRESSIVE_BUY", "AGGRESSIVE_SELL", or "NONE".

    This catches waterfall moves and parabolic rips — exactly the kind
    of market behavior where being on the wrong side costs the most.
    """
    try:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 18)
        if rates is None or len(rates) < 18:
            return "NONE"

        last3 = rates[-3:]
        prior15 = rates[-18:-3]

        # Condition 1: same direction
        bull_count = sum(1 for c in last3 if c["close"] > c["open"])
        bear_count = sum(1 for c in last3 if c["close"] < c["open"])
        if bull_count < 3 and bear_count < 3:
            return "NONE"

        # Condition 2: combined body > 2x avg
        last3_body = sum(abs(c["close"] - c["open"]) for c in last3)
        avg_body = sum(abs(c["close"] - c["open"]) for c in prior15) / len(prior15)
        if avg_body <= 0 or last3_body < avg_body * 2.0:
            return "NONE"

        # Condition 3: net move > 1.5x ATR
        atr = calculate_atr(symbol)
        if atr <= 0:
            return "NONE"
        net_move = abs(last3[-1]["close"] - last3[0]["open"])
        if net_move < atr * 1.5:
            return "NONE"

        if bull_count == 3: return "AGGRESSIVE_BUY"
        if bear_count == 3: return "AGGRESSIVE_SELL"
        return "NONE"
    except Exception:
        return "NONE"


def _m1_reversal_against(position) -> bool:
    """
    Strict multi-candle reversal detection. A pro scalper doesn't bail on a
    single counter-candle — that's just market noise. We only flag a real
    reversal when:
      1. Position has been open at least 90 seconds (no panic exits)
      2. Last 2 M1 candles BOTH closed against us (not just one)
      3. Combined body size > 2.0x recent average (strong, not noise)
      4. Net move over those 2 candles > 0.7x M15 ATR (meaningful range)

    This is intentionally hard to trigger. The SL exists for a reason —
    let it do its job on minor pullbacks. Only flag for real confirmed reversals.
    """
    try:
        # Rule 1: Don't bail on trades less than 90 seconds old
        if hasattr(position, "time") and position.time:
            age_sec = (datetime.now(timezone.utc) -
                      datetime.fromtimestamp(position.time, tz=timezone.utc)).total_seconds()
            if age_sec < 90:
                return False

        rates = mt5.copy_rates_from_pos(position.symbol, mt5.TIMEFRAME_M1, 0, 12)
        if rates is None or len(rates) < 10:
            return False

        last_2 = rates[-2:]
        baseline = rates[-12:-2]

        # Rule 2: Last 2 candles must BOTH be against position
        all_against = True
        for c in last_2:
            is_bull = c["close"] > c["open"]
            if position.type == mt5.POSITION_TYPE_BUY and is_bull:
                all_against = False; break
            if position.type == mt5.POSITION_TYPE_SELL and not is_bull:
                all_against = False; break
        if not all_against:
            return False

        # Rule 3: Combined body strength > 2x baseline average
        combined_body = sum(abs(c["close"] - c["open"]) for c in last_2)
        avg_body = sum(abs(c["close"] - c["open"]) for c in baseline) / len(baseline)
        if avg_body <= 0 or combined_body < avg_body * 2.0:
            return False

        # Rule 4: Net move > 0.7x ATR
        atr = calculate_atr(position.symbol)
        if atr <= 0:
            return False
        net_move = abs(last_2[-1]["close"] - last_2[0]["open"])
        if net_move < atr * 0.7:
            return False

        return True
    except Exception:
        return False


def manage_basket(positions: list) -> int:
    """
    Manages a list of OUR positions on the SAME symbol as a basket.
    Closes ALL positions when combined profit hits the basket target.

    Threshold = BASKET_TP_PER_001_LOT x (total basket lot / 0.01)
    Example: 3 x 0.02 lot XAU positions = 0.06 total lot = $9 trigger ($1.50 x 6)
    Example: 3 x 0.20 lot BTC positions = 0.60 total lot = $90 trigger ($1.50 x 60)

    Returns count of positions closed (0 if basket didn't trigger).
    """
    if not cfg.BASKET_TP_ENABLED:
        return 0
    if len(positions) < cfg.BASKET_TP_MIN_POSITIONS:
        return 0

    total_profit = sum(float(p.profit) for p in positions)
    total_lot    = sum(float(p.volume) for p in positions)
    if total_lot <= 0:
        return 0

    # Threshold scales with basket size
    threshold = cfg.BASKET_TP_PER_001_LOT * (total_lot / 0.01)

    if total_profit < threshold:
        return 0

    # Close every position in the basket
    closed = 0
    symbol = positions[0].symbol
    for pos in positions:
        if _close_position(pos, "BASKET_TP_LOCK") != "HOLD":
            closed += 1

    if closed > 0:
        logger.info("💼 BASKET_TP_LOCK %s | closed %d positions | total_profit=$%.2f (target=$%.2f) | total_lot=%.2f",
                   symbol, closed, total_profit, threshold, total_lot)
    return closed


def manage_position(position, login: Optional[int] = None) -> str:
    """
    All exit logic. Runs every cycle for every of OUR open positions.

    ★ CONSOLIDATED (this revision):
      1. Emergency SL net (no position ever runs naked)
      2. SCALE-OUT partials + runner trail (returns on action)
      3. UNIFIED PROFIT LOCK — one stop computed from ALL lock rules, at most
         ONE SLTP modify per cycle (previously four separate blocks each sent
         their own modify: PROFIT_LOCK, BREAKEVEN_PROTECT, BE_LOCK_$1,
         LOCK_PROFIT_$2 — brokers throttle exactly that)
      4. Reversal-candle bank, near-TP runner extension
      5. Smart loss exit (confirmed-bad-hold + aggressive-move recovery flip,
         now armed PER-LOGIN)
      6. Stale-flat cleanup
      7. LEGACY tier ladder — runs ONLY when SCALE_OUT_ENABLED is False
    """
    profit = float(position.profit)
    lot_mult = position.volume / 0.01
    is_buy = (position.type == mt5.POSITION_TYPE_BUY)
    action = "BUY" if is_buy else "SELL"

    # ── EMERGENCY SL NET — no position is EVER left without a stop ───────────
    # The -$294 loss happened because a position ran with no effective stop.
    # If ANY position we manage has no SL set, put one on NOW, bounded to a
    # safe distance (the same MAX_SL_PCT_OF_PRICE cap used at entry). This is
    # the last line of defense against a naked, account-killing runner.
    if not position.sl or position.sl == 0.0:
        try:
            tick_e = mt5.symbol_info_tick(position.symbol)
            if tick_e:
                ref = tick_e.bid if is_buy else tick_e.ask
                atr_e = calculate_atr(position.symbol, period=14)
                sl_dist = min(atr_e if atr_e > 0 else ref * 0.002,
                              ref * cfg.MAX_SL_PCT_OF_PRICE)
                emer_sl = (ref - sl_dist) if is_buy else (ref + sl_dist)
                mt5.order_send({
                    "action": mt5.TRADE_ACTION_SLTP, "symbol": position.symbol,
                    "position": position.ticket, "sl": emer_sl,
                    "tp": position.tp or 0.0,
                })
                logger.warning("🛡️ EMERGENCY SL set on %s ticket %s (was naked) sl=%.5f",
                              position.symbol, position.ticket, emer_sl)
        except Exception as e:
            logger.debug("emergency SL failed: %s", e)

    # Trade age
    trade_age_sec = 0
    if hasattr(position, "time") and position.time:
        try:
            opened = datetime.fromtimestamp(position.time, tz=timezone.utc)
            trade_age_sec = (datetime.now(timezone.utc) - opened).total_seconds()
        except Exception:
            pass

    # ════════════════════════════════════════════════════════════════════════
    # SCALE-OUT WINNER MANAGEMENT — the core strategy.
    # Bank partial profit in stages and let a runner ride. This banks something
    # on nearly every winner while letting the occasional big move run — that
    # asymmetry (small consistent wins + occasional large ones, tight losses)
    # is what actually compounds. profit_per_001 normalizes across lot sizes.
    # ════════════════════════════════════════════════════════════════════════
    if cfg.SCALE_OUT_ENABLED and profit > 0:
        profit_per_001 = profit / lot_mult if lot_mult > 0 else profit

        # Stage 1: +$2/0.01lot -> take 50% off, move SL to breakeven.
        if (profit_per_001 >= cfg.SCALE1_TRIGGER_USD
                and position.ticket not in _scale1_done):
            res = _partial_close(position, cfg.SCALE1_CLOSE_PCT, "SCALE1_50")
            if res in ("PARTIAL_CLOSED", "FULL_FROM_PARTIAL"):
                _scale1_done.add(position.ticket)
                # Move SL to breakeven on the remainder so it can't lose —
                # but never LOOSEN a profit-lock already set.
                if res == "PARTIAL_CLOSED" and _sl_is_better_generic(
                        position, position.price_open, is_buy):
                    _set_sl(position, position.price_open, "BE_AFTER_SCALE1")
                logger.info("💰 SCALE1 %s — banked 50%% at +$%.2f/0.01lot, runner to BE",
                           position.symbol, profit_per_001)
                return res

        # Stage 2: +$4/0.01lot -> take another 25%, lock SL at +$2/0.01lot.
        if (profit_per_001 >= cfg.SCALE2_TRIGGER_USD
                and position.ticket in _scale1_done
                and position.ticket not in _scale2_done):
            res = _partial_close(position, cfg.SCALE2_CLOSE_PCT, "SCALE2_25")
            if res in ("PARTIAL_CLOSED", "FULL_FROM_PARTIAL"):
                _scale2_done.add(position.ticket)
                if res == "PARTIAL_CLOSED":
                    # Lock +$2/0.01lot of profit on the runner
                    info0 = mt5.symbol_info(position.symbol)
                    if info0 and info0.trade_tick_value and info0.trade_tick_size:
                        usd_to_price = info0.trade_tick_size / (
                            info0.trade_tick_value * position.volume)
                        lock = cfg.SCALE2_LOCK_USD * lot_mult * usd_to_price
                        new_sl = (position.price_open + lock if is_buy
                                  else position.price_open - lock)
                        _set_sl(position, new_sl, "LOCK_$2_AFTER_SCALE2")
                logger.info("💰 SCALE2 %s — banked 25%% at +$%.2f/0.01lot, runner locked +$2",
                           position.symbol, profit_per_001)
                return res

        # Runner: after both scales, trail the final 25% behind its peak.
        if position.ticket in _scale2_done:
            peak = _runner_peak.get(position.ticket, profit_per_001)
            if profit_per_001 > peak:
                peak = profit_per_001
                _runner_peak[position.ticket] = peak
            # Confirmed reversal ends the runner
            if cfg.RUNNER_REVERSAL_CLOSE and detect_reversal_candle(position.symbol, action):
                return _close_position(position, "RUNNER_REVERSAL")
            # Trailing stop: if profit falls RUNNER_TRAIL_USD below peak, exit
            if profit_per_001 <= peak - cfg.RUNNER_TRAIL_USD:
                return _close_position(position, "RUNNER_TRAIL_HIT")
            # Otherwise keep riding — also push SL up behind price
            info0 = mt5.symbol_info(position.symbol)
            if info0 and info0.trade_tick_value and info0.trade_tick_size:
                usd_to_price = info0.trade_tick_size / (
                    info0.trade_tick_value * position.volume)
                trail_lock = (peak - cfg.RUNNER_TRAIL_USD) * lot_mult * usd_to_price
                if trail_lock > 0:
                    new_sl = (position.price_open + trail_lock if is_buy
                              else position.price_open - trail_lock)
                    if _sl_is_better_generic(position, new_sl, is_buy):
                        _set_sl(position, new_sl, "RUNNER_TRAIL_SL")
            return "RUNNER_RIDING"

    # Legacy hard-close fallback (only if scale-out is disabled)
    if (not cfg.SCALE_OUT_ENABLED) and profit >= cfg.SCALP_CLOSE_PROFIT_USD * lot_mult:
        return _close_position(position, "SCALP_TP_$2")

    # ── UNIFIED PROFIT LOCK — one stop calculation, at most ONE modify ───────
    # Consolidates the old PROFIT_LOCK + BREAKEVEN_PROTECT + BE_LOCK_$1 +
    # LOCK_PROFIT_$2 blocks (which each sent their own SLTP order per cycle).
    # Rules, expressed in absolute dollars, tightest wins:
    #   • at +$2 per 0.01 lot          -> lock +$1 per 0.01 lot (covers BE too)
    #   • at +$PROFIT_PROTECT_TRIGGER  -> lock (profit - GIVEBACK), floored at
    #                                     MIN_LOCK
    # The stop only ever ratchets toward profit (_sl_is_better_generic), so a
    # winner can never be handed back to a loss.
    if profit > 0:
        per001_lock = profit / lot_mult if lot_mult > 0 else profit
        abs_lock = 0.0
        if per001_lock >= cfg.EXIT_BE_LOCK_USD:
            abs_lock = 1.0 * lot_mult
        if profit >= cfg.PROFIT_PROTECT_TRIGGER_USD:
            abs_lock = max(abs_lock,
                           max(cfg.PROFIT_PROTECT_MIN_LOCK_USD,
                               profit - cfg.PROFIT_PROTECT_GIVEBACK_USD))
        if abs_lock > 0:
            info_pp = mt5.symbol_info(position.symbol)
            if (info_pp and info_pp.trade_tick_value and info_pp.trade_tick_size
                    and position.volume):
                usd_to_price = info_pp.trade_tick_size / (
                    info_pp.trade_tick_value * position.volume)
                lock_dist = abs_lock * usd_to_price
                new_sl = (position.price_open + lock_dist if is_buy
                          else position.price_open - lock_dist)
                if _sl_is_better_generic(position, new_sl, is_buy):
                    _set_sl(position, new_sl, "PROFIT_LOCK")

    # (B) REVERSAL CANDLE — close immediately when in profit and the chart
    # paints an engulfing-against. A real scalper doesn't wait for SL.
    if profit > 0.5 and cfg.HOLD_RUNNER_REVERSAL_CLOSE:
        if detect_reversal_candle(position.symbol, action):
            return _close_position(position, "REVERSAL_CANDLE")

    # (C) NEAR-TP RUNNER DECISION — if we're 95% of the way to TP, decide
    # whether to take the scalp or let it run. Aggressive continuation =
    # extend TP one more leg. Otherwise leave broker's TP in place.
    if position.tp and position.tp != 0:
        tp_distance_total = abs(position.tp - position.price_open)
        tp_distance_remaining = abs(position.tp -
            (position.price_current if hasattr(position, "price_current") else position.price_open))
        if tp_distance_total > 0:
            pct_to_tp = 1.0 - (tp_distance_remaining / tp_distance_total)
            already_extended = position.ticket in _runner_extended_tickets
            if (pct_to_tp >= cfg.HOLD_RUNNER_NEAR_TP_PCT
                    and not already_extended
                    and detect_aggressive_continuation(position.symbol, action)):
                # Extend TP one more leg
                extension = tp_distance_total
                if is_buy:
                    new_tp = position.tp + extension
                else:
                    new_tp = position.tp - extension
                if _set_tp(position, new_tp, "RUNNER_EXTEND"):
                    _runner_extended_tickets.add(position.ticket)
                    logger.info("🏃 RUNNER %s %s — extended TP one more leg",
                               position.symbol, action)
                    return "RUNNER_EXTENDED"

    # ─── SMART LOSS EXIT — close a loser ONLY when the market CONFIRMS it's
    # no longer good to hold. We don't bail on noise. We close only when
    # MULTIPLE independent reads agree the trade is wrong:
    #   - trade has had time to work (>= 90s)
    #   - we're in real drawdown (> $2/0.01lot)
    #   - the market BIAS has flipped against us (M1+M5)
    #   - AND momentum is against us OR a reversal/exhaustion against us prints
    if (trade_age_sec >= 90 and profit < -2.0 * lot_mult):
        mk = read_market(position.symbol)
        against_bias = (is_buy and mk["bias"] == "DOWN") or ((not is_buy) and mk["bias"] == "UP")
        against_mom  = (is_buy and mk["momentum"] == "DOWN") or ((not is_buy) and mk["momentum"] == "UP")
        against_rev  = (is_buy and mk["reversal_dn"]) or ((not is_buy) and mk["reversal_up"])
        # Confirmed bad-to-hold: bias flipped AND (momentum against OR reversal against)
        if against_bias and (against_mom or against_rev):
            _flag_recovery_flip(login, position, mk)
            return _close_position(position, "CONFIRMED_BAD_HOLD")
        # Hard waterfall fallback (kept): decisive aggressive move against us.
        agg = detect_aggressive_move(position.symbol)
        if agg == "AGGRESSIVE_SELL" and is_buy:
            _flag_recovery_flip(login, position, mk, force_dir="SELL")
            return _close_position(position, "AGGRESSIVE_SELL_DETECTED")
        if agg == "AGGRESSIVE_BUY" and (not is_buy):
            _flag_recovery_flip(login, position, mk, force_dir="BUY")
            return _close_position(position, "AGGRESSIVE_BUY_DETECTED")

    # ─── REVERSAL_EXIT: DISABLED ────────────────────────────────────────────
    # A pro scalper doesn't have an early-exit on losing trades — they have a
    # stop loss. Let it do its job.
    # if profit > -cfg.REVERSAL_EXIT_PROFIT_MAX * lot_mult and _m1_reversal_against(position):
    #     return _close_position(position, "REVERSAL_EXIT")

    # ─── STALE_FLAT: trade open >20min with negligible movement ────────────
    if trade_age_sec / 60 > cfg.STALE_MINUTES:
        band = cfg.STALE_USD_BAND * lot_mult
        if -band < profit < band:
            return _close_position(position, "STALE_FLAT")

    if profit <= 0:
        return "HOLD"

    # ════════════════════════════════════════════════════════════════════════
    # LEGACY TIER LADDER — runs ONLY when SCALE_OUT_ENABLED is False.
    # When scale-out is on, everything below is fully covered by the scale-out
    # stages + the unified profit lock above; running both ladders at once was
    # the "overlapping exit systems" bug (a winner got a 50% SCALE1 *and* a
    # 25% BANK_25 at the same +$2 trigger).
    # ════════════════════════════════════════════════════════════════════════
    if cfg.SCALE_OUT_ENABLED:
        return "HOLD"

    info = mt5.symbol_info(position.symbol)
    if not info or not info.trade_tick_value or not info.trade_tick_size:
        return "HOLD"
    price_per_dollar = info.trade_tick_size / (info.trade_tick_value * position.volume)

    def _sl_at_profit_lock(profit_usd_per_001lot: float) -> float:
        """Compute SL price that locks the given profit (per 0.01 lot)."""
        buf_price = profit_usd_per_001lot * lot_mult * price_per_dollar
        if position.type == mt5.POSITION_TYPE_BUY:
            return position.price_open + buf_price
        else:
            return position.price_open - buf_price

    def _sl_is_better(new_sl: float) -> bool:
        """True if new_sl is more protective than current SL."""
        if not position.sl:
            return True
        if position.type == mt5.POSITION_TYPE_BUY:
            return new_sl > position.sl
        else:
            return new_sl < position.sl

    # ─── Tier 1: Breakeven lock at +$2 (lock $0.50 profit) ─────────────────
    if profit >= cfg.EXIT_BE_LOCK_USD * lot_mult:
        new_sl = _sl_at_profit_lock(cfg.EXIT_BE_BUFFER_USD)
        if _sl_is_better(new_sl):
            _set_sl(position, new_sl, "BE_LOCK")

    # ─── Tier 2: 25% partial at +$2 ───────────────────────────────────────
    if (profit >= cfg.EXIT_PARTIAL1_USD * lot_mult
            and position.ticket not in _partial_closed_tickets
            and position.volume >= 0.02):
        if _partial_close(position, cfg.EXIT_PARTIAL1_PCT, "BANK_25") == "PARTIAL_CLOSED":
            _partial_closed_tickets.add(position.ticket)
            return "BANK_25"

    # ─── Tier 3: Profit lock at +$3.5 (lock $2 profit) ────────────────────
    if profit >= cfg.EXIT_PROFIT_LOCK_USD * lot_mult:
        new_sl = _sl_at_profit_lock(cfg.EXIT_PROFIT_LOCK_BUF)
        if _sl_is_better(new_sl):
            _set_sl(position, new_sl, "PROFIT_LOCK_$2")

    # ─── Tier 4: Another 25% partial at +$5 ───────────────────────────────
    if (profit >= cfg.EXIT_PARTIAL2_USD * lot_mult
            and position.ticket in _partial_closed_tickets
            and position.ticket not in _partial_closed_tier2
            and position.volume >= 0.02):
        if _partial_close(position, cfg.EXIT_PARTIAL2_PCT, "BANK_25_2") == "PARTIAL_CLOSED":
            _partial_closed_tier2.add(position.ticket)
            return "BANK_25_2"

    # ─── Tier 5: Trailing at +$7 (with $5 minimum lock guarantee) ─────────
    if profit >= cfg.EXIT_TRAIL_START_USD * lot_mult:
        strength = _m1_strength(position.symbol)
        step_usd = (cfg.TRAIL_STEP_STRONG if strength == "STRONG"
                   else cfg.TRAIL_STEP_WEAK if strength == "WEAK"
                   else cfg.TRAIL_STEP_MODERATE) * lot_mult
        step_price = step_usd * price_per_dollar
        tick = mt5.symbol_info_tick(position.symbol)
        if tick:
            # Floor: SL must lock at least EXIT_TRAIL_MIN_LOCK profit
            min_lock_sl = _sl_at_profit_lock(cfg.EXIT_TRAIL_MIN_LOCK)
            if position.type == mt5.POSITION_TYPE_BUY:
                trail_sl = tick.bid - step_price
                new_sl = max(trail_sl, min_lock_sl)
                if _sl_is_better(new_sl):
                    _set_sl(position, new_sl, f"TRAIL_{strength}")
                    return "TRAILING"
            else:
                trail_sl = tick.ask + step_price
                new_sl = min(trail_sl, min_lock_sl)
                if _sl_is_better(new_sl):
                    _set_sl(position, new_sl, f"TRAIL_{strength}")
                    return "TRAILING"

    return "HOLD"


# ==============================================================================
# ACCOUNT PROTECTION
# ==============================================================================
def account_protection_check(login: int, acct_info) -> Tuple[bool, str]:
    """Returns (ok, reason). Worker's safety gate before opening trades."""
    # Margin level check
    if acct_info.margin > 0:
        margin_level = (acct_info.equity / acct_info.margin) * 100
        if margin_level < cfg.MIN_MARGIN_LEVEL_PCT:
            return False, f"margin_level_low_{margin_level:.0f}%"

    # Free margin %
    if acct_info.balance > 0:
        free_pct = (acct_info.margin_free / acct_info.balance) * 100
        if free_pct < cfg.MIN_FREE_MARGIN_PCT:
            return False, f"free_margin_low_{free_pct:.0f}%"

    # Daily loss circuit breaker (off by default)
    if cfg.DAILY_LOSS_PCT_LIMIT > 0 and acct_info.balance > 0:
        loss = _daily_loss[(login, date.today())]
        loss_pct = (loss / acct_info.balance) * 100
        if loss_pct >= cfg.DAILY_LOSS_PCT_LIMIT:
            return False, f"daily_loss_limit_{loss_pct:.1f}%"

    # Equity drawdown from peak (off by default)
    if cfg.EQUITY_DRAWDOWN_PCT > 0:
        peak = _equity_peak.get(login, acct_info.equity)
        if acct_info.equity > peak:
            _equity_peak[login] = acct_info.equity
            peak = acct_info.equity
        if peak > 0:
            dd_pct = ((peak - acct_info.equity) / peak) * 100
            if dd_pct >= cfg.EQUITY_DRAWDOWN_PCT:
                return False, f"equity_dd_{dd_pct:.1f}%"

    return True, "ok"


def record_loss(login: int, amount: float) -> None:
    """Track daily losses for circuit breaker."""
    if amount < 0:
        _daily_loss[(login, date.today())] += abs(amount)


def record_outcome(login: int, symbol: str, profit: float) -> None:
    """Track per-symbol outcome history for anti-blind-entry gates."""
    key = (login, symbol)
    now = time.time()
    _recent_outcomes[key].append((now, profit))
    # Trim entries older than the consecutive-loss window
    cutoff = now - cfg.CONSECUTIVE_LOSS_WINDOW_SEC
    _recent_outcomes[key] = [(t, p) for t, p in _recent_outcomes[key] if t >= cutoff]

    # Check consecutive-loss circuit breaker
    losses_in_window = [(t, p) for t, p in _recent_outcomes[key] if p < -0.01]
    if len(losses_in_window) >= cfg.CONSECUTIVE_LOSS_LIMIT:
        _symbol_paused_until[key] = now + cfg.CONSECUTIVE_LOSS_PAUSE_SEC
        logger.warning("⛔ %d losses on %s for login %d — pausing this symbol for %d min",
                      len(losses_in_window), symbol, login,
                      cfg.CONSECUTIVE_LOSS_PAUSE_SEC // 60)


def scalper_brain_decision(broker_sym: str, action: str, signal,
                          scalp_distance: float, signal_entry: float,
                          signal_created_at) -> Tuple[bool, str]:
    """
    THE SECOND BRAIN.

    Acts like a 10-year-experience forex scalper deciding whether to take
    the entry NOW. Looks at live M1 price action and confirms (or refuses)
    the watcher's signal. Returns (should_take, reason).

    Refuses entry if:
      1. Confidence below threshold (BRAIN_MIN_CONFIDENCE)
      2. Signal is stale (older than BRAIN_SIGNAL_MAX_AGE_SEC)
      3. Spread too wide vs scalp target
      4. ATR too small (dead market) or too large (news event)
      5. Price already chased >2x ATR past signal entry
      6. Momentum disagrees with signal direction (chop / wrong way)
      7. Price near a recent rejection level

    Each check is short-circuit — first failure returns its reason.
    """
    if not cfg.BRAIN_ENABLED:
        return True, "brain_disabled"

    # 1) Confidence
    conf = getattr(signal, "confidence", 0) or 0
    if conf < cfg.BRAIN_MIN_CONFIDENCE:
        return False, f"brain_low_confidence_{conf}<{cfg.BRAIN_MIN_CONFIDENCE}"

    # 2) Signal freshness
    try:
        if signal_created_at:
            sig_age_s = (datetime.now(timezone.utc) - signal_created_at).total_seconds()
            if sig_age_s > cfg.BRAIN_SIGNAL_MAX_AGE_SEC:
                return False, f"brain_stale_signal_{int(sig_age_s)}s"
    except Exception:
        pass

    # 3) Spread check vs TP
    tick = mt5.symbol_info_tick(broker_sym)
    if not tick:
        return False, "brain_no_tick"
    spread = abs(tick.ask - tick.bid)
    if scalp_distance > 0:
        spread_pct = spread / scalp_distance
        if spread_pct > cfg.BRAIN_MAX_SPREAD_PCT_OF_TP:
            return False, f"brain_wide_spread_{spread_pct:.0%}>{cfg.BRAIN_MAX_SPREAD_PCT_OF_TP:.0%}"

    # 4) ATR sanity
    atr = calculate_atr(broker_sym, period=14)
    if atr <= 0:
        return False, "brain_no_atr"

    # ─── SPREAD-QUALITY GATE — trade only when conditions favour the edge ───
    # The 3-year backtest proved this strategy LOSES when spread is large
    # relative to volatility and WINS when spread is small. Standing down when
    # spread/ATR is too high turned every losing year profitable.
    try:
        tick_q = mt5.symbol_info_tick(broker_sym)
        if tick_q:
            spread_px = abs(tick_q.ask - tick_q.bid)
            if atr > 0 and (spread_px / atr) > cfg.MAX_SPREAD_ATR_RATIO:
                return False, f"brain_spread_too_wide_{round(spread_px/atr*100)}pct_of_atr"
    except Exception:
        pass
    if scalp_distance > 0:
        atr_ratio = atr / scalp_distance
        if atr_ratio < cfg.BRAIN_MIN_ATR_RATIO:
            return False, f"brain_dead_market_atr_{atr_ratio:.2f}"
        if atr_ratio > cfg.BRAIN_MAX_ATR_RATIO:
            return False, f"brain_too_volatile_atr_{atr_ratio:.2f}"

    # 5) Chase guard + PRICE SANITY — current price vs signal entry
    current = tick.ask if action == "BUY" else tick.bid
    if signal_entry and signal_entry > 0:
        # Sanity check FIRST — if signal price differs from current by more
        # than BRAIN_PRICE_SANITY_PCT, the signal is corrupted (e.g. watcher
        # mixing up BTCJPY price into BTCUSD signal). Refuse hard.
        price_diff_pct = abs(current - signal_entry) / max(current, 1e-9)
        if price_diff_pct > cfg.BRAIN_PRICE_SANITY_PCT:
            return False, f"brain_signal_price_corrupted_{price_diff_pct:.0%}_off"

        chase = abs(current - signal_entry)
        chase_mult = chase / atr if atr > 0 else 0
        if chase_mult > cfg.BRAIN_MAX_CHASE_ATR_MULT:
            return False, f"brain_chasing_{chase_mult:.1f}x_atr_past_signal"
        # Also refuse if price has moved AGAINST the signal materially
        if action == "BUY" and current < signal_entry - 0.5 * atr:
            return False, "brain_signal_invalidated_price_dropped"
        if action == "SELL" and current > signal_entry + 0.5 * atr:
            return False, "brain_signal_invalidated_price_rose"

    # 6) Momentum confirmation — SOFT. We don't demand the signal's direction
    # be confirmed; we only REFUSE if price is moving STRONGLY against the
    # signal (all 3 closed M1 candles hard against + last body big).
    if cfg.BRAIN_REQUIRE_MOMENTUM:
        try:
            rates = mt5.copy_rates_from_pos(
                broker_sym, mt5.TIMEFRAME_M1, 0,
                cfg.BRAIN_MOMENTUM_M1_LOOKBACK + 2
            )
            if rates is not None and len(rates) >= cfg.BRAIN_MOMENTUM_M1_LOOKBACK + 1:
                lookback = cfg.BRAIN_MOMENTUM_M1_LOOKBACK
                closed = rates[-(lookback + 1):-1]
                bodies = [(r["close"] - r["open"]) for r in closed]
                big = atr * 0.6
                if action == "BUY":
                    # refuse only if ALL candles down AND last one strongly down
                    all_against = all(b < 0 for b in bodies)
                    strong_against = bodies[-1] < -big
                    if all_against and strong_against:
                        return False, "brain_strong_downtrend_vs_buy"
                else:
                    all_against = all(b > 0 for b in bodies)
                    strong_against = bodies[-1] > big
                    if all_against and strong_against:
                        return False, "brain_strong_uptrend_vs_sell"
        except Exception as e:
            logger.debug("brain momentum check failed: %s", e)

    # 7) Recent rejection check
    try:
        rates = mt5.copy_rates_from_pos(
            broker_sym, mt5.TIMEFRAME_M1, 0, cfg.BRAIN_REJECTION_LOOKBACK
        )
        if rates is not None and len(rates) > 5:
            rejection_zone = scalp_distance * cfg.BRAIN_REJECTION_PROXIMITY
            for r in rates[:-2]:   # ignore last 2 bars (could be us)
                if action == "BUY":
                    # Wick high near current = recent supply
                    if abs(r["high"] - current) < rejection_zone and r["close"] < r["open"]:
                        return False, "brain_recent_rejection_above"
                else:
                    if abs(r["low"] - current) < rejection_zone and r["close"] > r["open"]:
                        return False, "brain_recent_rejection_below"
    except Exception as e:
        logger.debug("brain rejection check failed: %s", e)

    # ─── INTELLIGENT ENTRY FILTER — don't fight an exhausted/reversing move ───
    # Surgical AND-logic so normal entries still flow (keeps trade frequency
    # up): only refuse when a reversal AGAINST us is confirmed AND (exhaustion
    # OR momentum has flipped against us). One signal alone never blocks.
    try:
        mk = read_market(broker_sym)
        conf = float(getattr(signal, "confidence", 0) or 0)
        if action == "SELL":
            # Selling into a forming bottom/bounce
            if mk["reversal_up"] and (mk["exhaustion"] or mk["momentum"] == "UP"):
                return False, "brain_selling_into_reversal_up"
            # Overextended DOWN — only skip if the move is also STALLING.
            if mk["extended_dn"] and conf < 85 and (
                    mk["momentum"] != "DOWN" or mk["reversal_up"] or mk["exhaustion"]):
                return False, f"brain_selling_exhausted_dn_{mk['extension_atr']}atr"
        else:  # BUY
            # Buying into a forming top/drop
            if mk["reversal_dn"] and (mk["exhaustion"] or mk["momentum"] == "DOWN"):
                return False, "brain_buying_into_reversal_dn"
            # Overextended UP — only skip if also stalling (see SELL logic above).
            if mk["extended_up"] and conf < 85 and (
                    mk["momentum"] != "UP" or mk["reversal_dn"] or mk["exhaustion"]):
                return False, f"brain_buying_exhausted_up_{mk['extension_atr']}atr"
    except Exception as e:
        logger.debug("brain entry-reversal check failed: %s", e)

    return True, "brain_approved"


def _read_market_uncached(broker_sym: str) -> dict:
    """
    THE MARKET-READ — a compact, intelligent snapshot the brain uses to
    understand what price is actually doing RIGHT NOW, across M1 and M5.

    Returns a dict:
      bias        : "UP" | "DOWN" | "FLAT"   — net directional read (M1+M5)
      strength    : 0..100                    — how convincing the bias is
      momentum    : "UP" | "DOWN" | "FLAT"    — last-few-candle thrust (M1)
      reversal_up : bool  — bullish reversal forming
      reversal_dn : bool  — bearish reversal forming
      exhaustion  : bool  — long wick / overextension (move likely to stall)
      atr         : float — M15 ATR(14)

    Designed to be cheap (two copy_rates calls) and tolerant — on any error
    it returns a FLAT/neutral read so it never blocks trading.
    """
    out = {"bias": "FLAT", "strength": 0, "momentum": "FLAT",
           "reversal_up": False, "reversal_dn": False,
           "exhaustion": False, "atr": 0.0,
           "extension_atr": 0.0, "extended_up": False, "extended_dn": False}
    try:
        atr = calculate_atr(broker_sym, period=14)
        out["atr"] = atr
        m1 = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M1, 0, 12)
        m5 = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M5, 0, 8)
        if m1 is None or len(m1) < 8:
            return out

        closed1 = m1[:-1]              # closed M1 candles
        last = closed1[-1]
        prev = closed1[-2]

        # ── Trend bias: EMA-ish slope on M1 closes + M5 confirmation ──────
        c1 = [r["close"] for r in closed1]
        m1_slope = c1[-1] - c1[0]
        score = 0
        if m1_slope > 0: score += 1
        elif m1_slope < 0: score -= 1
        if m5 is not None and len(m5) >= 5:
            c5 = [r["close"] for r in m5[:-1]]
            m5_slope = c5[-1] - c5[0]
            if m5_slope > 0: score += 2      # higher timeframe weighs more
            elif m5_slope < 0: score -= 2
        if score >= 2:   out["bias"] = "UP"
        elif score <= -2: out["bias"] = "DOWN"
        else:            out["bias"] = "FLAT"
        out["strength"] = min(100, abs(score) * 25)

        # ── Momentum: last 3 closed M1 bodies ─────────────────────────────
        bodies = [r["close"] - r["open"] for r in closed1[-3:]]
        if all(b > 0 for b in bodies):   out["momentum"] = "UP"
        elif all(b < 0 for b in bodies): out["momentum"] = "DOWN"
        else:                            out["momentum"] = "FLAT"

        # ── Reversal patterns (engulfing + pin) on last closed candle ─────
        last_body = abs(last["close"] - last["open"])
        prev_body = abs(prev["close"] - prev["open"])
        rng = last["high"] - last["low"]
        upper_wick = last["high"] - max(last["close"], last["open"])
        lower_wick = min(last["close"], last["open"]) - last["low"]

        bull_engulf = (last["close"] > last["open"] and prev["close"] < prev["open"]
                       and last_body > prev_body * 1.1)
        bear_engulf = (last["close"] < last["open"] and prev["close"] > prev["open"]
                       and last_body > prev_body * 1.1)
        bull_pin = rng > 0 and lower_wick > rng * 0.6      # long lower wick
        bear_pin = rng > 0 and upper_wick > rng * 0.6      # long upper wick

        out["reversal_up"] = bool(bull_engulf or bull_pin)
        out["reversal_dn"] = bool(bear_engulf or bear_pin)
        # Exhaustion: a big candle with a big opposing wick = stalling
        if atr > 0 and last_body > atr * 1.2 and (upper_wick > last_body * 0.5
                                                   or lower_wick > last_body * 0.5):
            out["exhaustion"] = True

        # ── Extension: how far price has stretched from its recent mean, in
        # ATR units. A big straight move is OVEREXTENDED — the easy move is
        # done and a bounce is overdue, so entering WITH the move here means
        # chasing the bottom/top.
        try:
            if m5 is not None and len(m5) >= 6 and atr > 0:
                ref_closes = [r["close"] for r in m5[:-1]]
                mean_ref = sum(ref_closes) / len(ref_closes)
                cur = closed1[-1]["close"]
                ext_atr = (cur - mean_ref) / atr   # +ve above mean, -ve below
                out["extension_atr"] = round(ext_atr, 2)
                # 2.0 ATR from the mean = clearly stretched
                out["extended_up"] = ext_atr > 2.0
                out["extended_dn"] = ext_atr < -2.0
        except Exception:
            pass
    except Exception as e:
        logger.debug("read_market failed for %s: %s", broker_sym, e)
    return out


def read_market(broker_sym: str) -> dict:
    """Cycle-cached market read. The snapshot is derived from CLOSED M1/M5
    candles, so it's identical for every user within a short window — the first
    user to need it pays the two copy_rates round-trips; the rest reuse it.
    A neutral/FLAT read (bias FLAT + atr 0) is treated as a soft failure and
    NOT cached, so a momentary fetch glitch can't pin a whole window to FLAT.
    Returns a COPY so no caller can mutate the shared cached dict."""
    key = ("mkt", broker_sym)
    cached = _mkt_cache_get(key)
    if cached is not None:
        return dict(cached)
    out = _read_market_uncached(broker_sym)
    if out and not (out.get("bias") == "FLAT" and out.get("atr", 0) == 0.0):
        _mkt_cache_put(key, dict(out))
    return out


def detect_reversal_candle(broker_sym: str, action: str) -> bool:
    """
    Returns True if the last CLOSED M1 candle is an engulfing-against the
    open position direction. A real scalper closes IMMEDIATELY on this.
    """
    try:
        rates = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M1, 0, 3)
        if rates is None or len(rates) < 3:
            return False
        prev, last = rates[-3], rates[-2]  # last CLOSED candle
        last_body = abs(last["close"] - last["open"])
        prev_body = abs(prev["close"] - prev["open"])
        if last_body < prev_body * 1.2:
            return False  # not engulfing
        if action == "BUY":
            # Bearish engulfing against our long
            return (last["close"] < last["open"]) and (prev["close"] > prev["open"])
        else:
            # Bullish engulfing against our short
            return (last["close"] > last["open"]) and (prev["close"] < prev["open"])
    except Exception:
        return False


def detect_aggressive_continuation(broker_sym: str, action: str) -> bool:
    """
    Returns True if recent price action shows aggressive continuation in
    the position's direction — the kind of move that justifies extending
    TP past the 5-pip scalp target.

    Criteria: last closed candle body > HOLD_RUNNER_BODY_VS_ATR x ATR AND
    last 3 candles all in the same direction as the position.
    """
    try:
        atr = calculate_atr(broker_sym, period=14)
        rates = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M1, 0, 5)
        if rates is None or len(rates) < 4 or atr <= 0:
            return False
        last_candles = rates[-4:-1]  # last 3 closed
        bodies = [r["close"] - r["open"] for r in last_candles]
        last_body = bodies[-1]
        # All same direction as position?
        if action == "BUY":
            all_up = all(b > 0 for b in bodies)
            strong = last_body > cfg.HOLD_RUNNER_BODY_VS_ATR * atr
            return all_up and strong
        else:
            all_down = all(b < 0 for b in bodies)
            strong = last_body < -cfg.HOLD_RUNNER_BODY_VS_ATR * atr
            return all_down and strong
    except Exception:
        return False


def check_entry_gates(login: int, symbol: str, action: str,
                     same_dir_positions: list) -> Optional[str]:
    """
    Anti-blind-entry checks. Returns block reason or None if entry is allowed.
    Runs after the watcher's signal is confirmed fresh and confident.
    """
    key = (login, symbol)
    now = time.time()

    # Gate 1: Symbol paused due to consecutive losses
    if _symbol_paused_until.get(key, 0) > now:
        remaining = int(_symbol_paused_until[key] - now)
        return f"symbol_paused_after_losses_{remaining}s_remaining"

    # Gate 2: Recent loss cooldown (no immediate re-entry after a loss)
    recent = _recent_outcomes.get(key, [])
    if recent:
        last_time, last_profit = recent[-1]
        if last_profit < -0.01 and (now - last_time) < cfg.RECENT_LOSS_COOLDOWN_SEC:
            cooldown_left = int(cfg.RECENT_LOSS_COOLDOWN_SEC - (now - last_time))
            return f"recent_loss_cooldown_{cooldown_left}s"

    # Gate 3: Don't MARTINGALE into a losing same-direction position.
    # Allow the mode's intended stacking but refuse to add to a same-direction
    # position that's already meaningfully underwater (>$3 per 0.01 lot).
    for pos in same_dir_positions:
        lot_mult = max(pos.volume / 0.01, 1.0)
        max_dd = cfg.EXISTING_LOSER_MAX_DD_USD * lot_mult
        if pos.profit < -max_dd:
            return f"existing_position_down_${abs(pos.profit):.2f}_no_martingale"

    return None


# ==============================================================================
# LEARNING LOOP
# ==============================================================================
def refresh_learning(db: Session) -> None:
    global _learning_last_refresh
    try:
        rows = db.query(AITradeHistory).filter(
            AITradeHistory.status == "CLOSED",
            AITradeHistory.created_at >= datetime.now(timezone.utc) - timedelta(days=cfg.LEARNING_LOOKBACK_DAYS),
        ).all()
        sym_stats = defaultdict(lambda: [0, 0])
        setup_stats = defaultdict(lambda: [0, 0])
        for row in rows:
            outcome = (row.result or "").upper()
            if outcome not in ("WIN", "LOSS"): continue
            if row.symbol:
                sym_stats[row.symbol][1] += 1
                if outcome == "WIN": sym_stats[row.symbol][0] += 1
            setup = getattr(row, "setup_type", None) or "UNKNOWN"
            setup_stats[setup][1] += 1
            if outcome == "WIN": setup_stats[setup][0] += 1
        _symbol_winrate.clear(); _symbol_trades.clear()
        for s, (w, t) in sym_stats.items():
            _symbol_trades[s] = t
            _symbol_winrate[s] = w / t if t > 0 else 0.5
        _setup_winrate.clear(); _setup_trades.clear()
        for s, (w, t) in setup_stats.items():
            _setup_trades[s] = t
            _setup_winrate[s] = w / t if t > 0 else 0.5
        _learning_last_refresh = time.time()
        notable = [(s, _symbol_winrate[s], _symbol_trades[s])
                   for s in _symbol_winrate
                   if _symbol_trades[s] >= cfg.LEARNING_MIN_TRADES]
        if notable:
            logger.info("🧠 LEARNING: %s",
                       ", ".join(f"{s}={int(wr*100)}%x{n}" for s, wr, n in notable[:10]))
    except Exception as e:
        logger.error("Learning refresh failed: %s", e)


def learning_lot_multiplier(symbol: str, setup: Optional[str]) -> float:
    wr = _symbol_winrate.get(symbol)
    n  = _symbol_trades.get(symbol, 0)
    if n >= cfg.LEARNING_MIN_TRADES:
        if wr >= cfg.LEARNING_BOOST_WR:  return cfg.LEARNING_BOOST_FACTOR
        if wr <= cfg.LEARNING_REDUCE_WR: return cfg.LEARNING_REDUCE_FACTOR
    if setup:
        swr = _setup_winrate.get(setup)
        sn  = _setup_trades.get(setup, 0)
        if sn >= cfg.LEARNING_MIN_TRADES:
            if swr >= cfg.LEARNING_BOOST_WR:  return cfg.LEARNING_BOOST_FACTOR
            if swr <= cfg.LEARNING_REDUCE_WR: return cfg.LEARNING_REDUCE_FACTOR
    return 1.0


def write_trade_outcome(db: Session, trade: LiveTrade, exit_reason: str,
                       actual_profit: float, signal: Optional[AISignal] = None,
                       close_price: float = None) -> None:
    try:
        if trade.entry_price and trade.stop_loss:
            risk = abs(trade.entry_price - trade.stop_loss)
            if actual_profit >= 0 and trade.lot_size:
                reward = abs(actual_profit / (trade.lot_size * 100))
                actual_rr = reward / risk if risk else 0
            else:
                actual_rr = -1.0
        else:
            actual_rr = 0.0
        planned_rr = 0
        if (trade.entry_price and trade.stop_loss and trade.take_profit
                and trade.entry_price != trade.stop_loss):
            planned_rr = abs(trade.take_profit - trade.entry_price) / \
                         abs(trade.entry_price - trade.stop_loss)
        dur_secs = 0
        if trade.opened_at:
            now = datetime.now(timezone.utc)
            opened = trade.opened_at if trade.opened_at.tzinfo else trade.opened_at.replace(tzinfo=timezone.utc)
            dur_secs = (now - opened).total_seconds()
        result = "WIN" if actual_profit > 0.01 else "LOSS" if actual_profit < -0.01 else "BREAKEVEN"

        # Resolve the per-user license_id from the trade (LiveTrade has
        # license_key). AISignal is GLOBAL — it has no license_id — so the
        # old code's `signal.license_id` was always None, which made every
        # AITradeHistory row orphaned (no license_id), invisible to the
        # /trade-history and /signals-pro endpoints.
        resolved_license_id = None
        try:
            if trade.license_key:
                lic_row = db.query(License).filter(
                    License.license_key == trade.license_key
                ).first()
                if lic_row:
                    resolved_license_id = lic_row.id
        except Exception:
            pass

        history = AITradeHistory(
            license_id = resolved_license_id,
            mt5_login  = trade.mt5_login,
            symbol     = trade.symbol,
            signal     = trade.trade_type,
            trend      = signal.trend if signal else None,
            entry_price = float(trade.entry_price) if trade.entry_price else 0,
            stop_loss  = float(trade.stop_loss) if trade.stop_loss else None,
            take_profit = float(trade.take_profit) if trade.take_profit else None,
            confidence = int(signal.confidence) if signal and signal.confidence else 0,
            result     = result, status = "CLOSED",
            profit     = float(actual_profit),
            lot_size   = float(trade.lot_size) if trade.lot_size else 0.01,
            created_at = trade.opened_at or datetime.now(timezone.utc),
            closed_at  = datetime.now(timezone.utc),
        )
        for field, val in [
            ("setup_type",       signal.entry_quality if signal else None),
            ("regime",           signal.structure if signal else None),
            ("exit_reason",      exit_reason),
            ("planned_rr",       float(planned_rr)),
            ("actual_rr",        float(actual_rr)),
            ("duration_minutes", int(dur_secs / 60)),
            ("close_price",      float(close_price) if close_price else None),
        ]:
            if hasattr(history, field):
                setattr(history, field, val)
        db.add(history)
        trade.status = "CLOSED"
        trade.profit = float(actual_profit)
        if close_price and hasattr(trade, "close_price"):
            trade.close_price = float(close_price)
        trade.closed_at = datetime.now(timezone.utc)
        db.commit()
        try:
            record_loss(int(trade.mt5_login), actual_profit)
            record_outcome(int(trade.mt5_login), trade.symbol, actual_profit)
        except (ValueError, TypeError):
            pass
        logger.info("📝 OUTCOME | %s | %s | $%.2f | %s",
                   trade.symbol, result, actual_profit, exit_reason)
    except Exception as e:
        logger.error("Outcome write failed: %s", e, exc_info=True)
        db.rollback()


# Cent accounts (currency USC / USX / EUX / etc.) report profit in CENTS, so a
# real $1.10 result comes back as 110. Detect this and scale to real dollars so
# reconciliation records the TRUE profit.
_CENT_CURRENCIES = {"USC", "USX", "EUX", "GBX", "CENT"}

def _cent_account_divisor(acct_info) -> float:
    try:
        cur = (getattr(acct_info, "currency", "") or "").upper()
        return 100.0 if cur in _CENT_CURRENCIES else 1.0
    except Exception:
        return 1.0


def fetch_final_profit(ticket_str: str, expected_max_abs: float = None) -> float:
    """
    Fetch realized profit for a closed position.
    Tries position-id first (modern MT5), falls back to order-id.
    """
    try:
        ticket = int(ticket_str)
        from_date = datetime.now(timezone.utc) - timedelta(days=7)
        # Method 1: query by position id
        deals = mt5.history_deals_get(from_date, datetime.now(timezone.utc), position=ticket)
        if not deals:
            # Method 2: find the deal by order ticket and use its position id
            order_deals = mt5.history_deals_get(from_date, datetime.now(timezone.utc), ticket=ticket)
            if order_deals:
                position_id = order_deals[0].position_id
                deals = mt5.history_deals_get(from_date, datetime.now(timezone.utc), position=position_id)
        if not deals:
            return 0.0

        # Only sum EXIT deals (entry == DEAL_ENTRY_OUT / OUT_BY) to avoid
        # summing opens + partial-close intermediates.
        try:
            close_deals = [d for d in deals if d.entry in (1, 2)]  # OUT or OUT_BY
            if close_deals:
                deals = close_deals
        except Exception:
            pass

        total = sum(float(d.profit) + float(d.swap) + float(d.commission)
                   for d in deals)
        total = round(total, 2)

        if expected_max_abs is not None and abs(total) > expected_max_abs:
            logger.warning(
                "⚠️ profit sanity note — got $%.2f for ticket %s (expected <=$%.2f).",
                total, ticket_str, expected_max_abs,
            )
        return total
    except Exception:
        return 0.0


def _confirm_closed_and_profit(ticket_str: str) -> Tuple[bool, float, float]:
    """
    Confirm a position is ACTUALLY closed before we record an outcome.

    Returns (closed, realized_profit, close_price). A position is only "closed"
    if MT5's deal history contains an EXIT deal (entry == OUT/OUT_BY) for it.
    close_price is the price of the exit deal (the REAL MT5 close), so the
    dashboard can show the true close instead of a dash.

    This prevents the bug where a still-open trade whose position-id shifted
    (common on BTC/GOLD partial closes) gets falsely recorded as a loss with a
    garbage profit pulled from the wrong deal.
    """
    try:
        ticket = int(ticket_str)
        from_date = datetime.now(timezone.utc) - timedelta(days=7)
        now = datetime.now(timezone.utc)

        deals = mt5.history_deals_get(from_date, now, position=ticket)
        if not deals:
            order_deals = mt5.history_deals_get(from_date, now, ticket=ticket)
            if order_deals:
                position_id = order_deals[0].position_id
                deals = mt5.history_deals_get(from_date, now, position=position_id)
        if not deals:
            return (False, 0.0, 0.0)

        out_deals = [d for d in deals if d.entry in (1, 2)]  # OUT / OUT_BY
        if not out_deals:
            return (False, 0.0, 0.0)

        realized = round(sum(float(d.profit) + float(d.swap) + float(d.commission)
                            for d in out_deals), 2)
        # Real close price = price of the last exit deal
        close_price = float(out_deals[-1].price or 0.0)
        return (True, realized, close_price)
    except Exception:
        return (False, 0.0, 0.0)


def infer_exit_reason(trade: LiveTrade, profit: float) -> str:
    """
    Pure-DB inference (limited — just based on profit + whether SL was set).
    Keep this simple and conservative: never claim SL_HIT, just say LOSS_CLOSE.
    """
    if profit > 0.01: return "PROFIT_CLOSE"
    if profit < -0.01: return "LOSS_CLOSE"
    return "BREAKEVEN_CLOSE"


def fetch_close_reason_from_mt5(ticket_str: str) -> Optional[str]:
    """
    Look at the closing deal's comment in MT5 history.
    Returns the comment string from the close deal, or None.
    """
    try:
        ticket = int(ticket_str)
        from_date = datetime.now(timezone.utc) - timedelta(days=7)
        deals = mt5.history_deals_get(from_date, datetime.now(timezone.utc), position=ticket)
        if not deals:
            return None
        # Find closing deals (entry=DEAL_ENTRY_OUT)
        closes = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
        if not closes:
            return None
        # Most recent close
        latest = max(closes, key=lambda d: d.time)
        comment = (latest.comment or "").lower()
        if "tp" in comment or "take profit" in comment or "[tp" in comment:
            return "TP_HIT"
        if "sl" in comment or "stop loss" in comment or "[sl" in comment:
            return "SL_HIT"
        if "nolimitz" in comment:
            # Extract our reason (e.g. "Nolimitz Ai - AGGRESSIVE_SELL_DETECTED")
            if " - " in latest.comment:
                return latest.comment.split(" - ", 1)[1]
            return "MANUAL_CLOSE"
        return "BROKER_CLOSE"
    except Exception:
        return None

# ==============================================================================
# MANUAL (SELF-EXECUTION) TRADES — from the AI Chart Scanner
# ==============================================================================
def _execute_manual_trade(db, account, acct_info, risk_mode, req) -> None:
    """
    Execute ONE user-initiated manual trade. Reuses the worker's safe
    primitives — enabled-symbol gate, no-hedge, per-symbol/total/notional caps,
    compute_lot (exact + balance cap), LIVE-price bounded SL/TP, and send_order
    (retry + filling rotation + emergency SL). Marks the request DONE or FAILED.
    """
    login_int = int(account.login)
    login_str = str(account.login)
    action = (req.action or "").upper().strip()

    def _fail(reason: str) -> None:
        try:
            req.status = "FAILED"
            req.error = reason[:200]
            req.processed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            db.rollback()
        logger.info("📨 MANUAL %s %s FAILED — %s", action, req.symbol, reason)

    def _done(ticket) -> None:
        try:
            req.status = "DONE"
            req.mt5_ticket = str(ticket)
            req.processed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            db.rollback()
        logger.info("📨✅ MANUAL %s %s DONE — ticket=%s", action, req.symbol, ticket)

    if action not in ("BUY", "SELL"):
        return _fail("invalid_action")
    if account.license_id is None:
        return _fail("no_license")

    symbol = (req.symbol or "").upper().replace(".A", "").replace(".M", "")

    setting = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == account.license_id,
        ClientSymbolSetting.symbol_name == symbol,
    ).first()
    if not setting or not setting.enabled:
        return _fail(f"symbol_{symbol}_not_enabled")
    # Only trade symbols the user enabled AND set a lot for (same rule as auto).
    if parse_user_lot(setting) is None:
        return _fail(f"symbol_{symbol}_no_lot_set")

    broker_sym = find_broker_symbol(symbol)
    if not broker_sym:
        return _fail(f"broker_symbol_not_found_{symbol}")
    mt5.symbol_select(broker_sym, True)
    vtick = mt5.symbol_info_tick(broker_sym)
    vrates = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M1, 0, 5)
    if (not vtick or vtick.bid <= 0 or vrates is None or len(vrates) < 3):
        return _fail(f"no_market_data_{broker_sym}")

    sym_class = classify_symbol(broker_sym)

    allpos = mt5.positions_get() or []
    opp = 0
    same_vol = 0.0
    same_cnt = 0
    for p in allpos:
        if p.magic != cfg.MAGIC:
            continue
        if classify_symbol(p.symbol) != sym_class:
            continue
        p_is_buy = (p.type == mt5.POSITION_TYPE_BUY)
        if p_is_buy != (action == "BUY"):
            opp += 1
        else:
            same_vol += float(p.volume)
            same_cnt += 1

    if opp > 0:
        return _fail(f"opposite_{sym_class}_position_open_close_it_first")

    # Count + volume cap (same risk-mode enforcement as the auto path).
    user_max = parse_max_trades(setting, risk_mode)
    hard_cap = cfg.HARD_MAX_SAME_DIR_PER_SYMBOL.get(
        sym_class, cfg.HARD_MAX_SAME_DIR_PER_SYMBOL["OTHER"])
    max_trades = min(user_max, hard_cap)
    if same_cnt >= max_trades:
        return _fail(f"max_trades_reached_{same_cnt}_of_{max_trades}_on_{sym_class}")
    per_trade_lot = parse_user_lot(setting) or cfg.LOT_MIN
    max_total_vol = round(max_trades * per_trade_lot, 2)
    if round(same_vol, 2) + per_trade_lot > max_total_vol + 1e-9:
        return _fail(f"vol_cap_{round(same_vol,2)}_of_{max_total_vol}_on_{sym_class}")

    ours_total = [p for p in allpos if p.magic == cfg.MAGIC]
    if len(ours_total) >= cfg.HARD_MAX_TOTAL_POSITIONS:
        return _fail(f"hard_cap_total_{len(ours_total)}")

    user_lot = parse_user_lot(setting)
    lot, lot_source = compute_lot(
        broker_sym, user_lot, risk_mode,
        account_balance=float(acct_info.balance or 1000.0),
    )
    if not lot or lot <= 0:
        return _fail(lot_source or "lot_zero_unaffordable")

    try:
        balance = float(acct_info.balance or 1.0)
        existing_notional = sum(float(p.volume) * float(p.price_open) for p in ours_total)
        cand_tick = mt5.symbol_info_tick(broker_sym)
        cand_price = float(cand_tick.ask) if cand_tick else 0.0
        total_after = existing_notional + lot * cand_price
        if balance < 500:
            lev_cap = 10.0
        elif balance < 2000:
            lev_cap = 20.0
        else:
            lev_cap = cfg.HARD_MAX_NOTIONAL_PER_BALANCE
        if total_after > lev_cap * balance:
            return _fail(f"notional_cap_${total_after:.0f}_>_${lev_cap*balance:.0f}")
    except Exception:
        pass

    entry = vtick.ask if action == "BUY" else vtick.bid
    atr = calculate_atr(broker_sym, period=14)
    sl_dist = min(atr if atr > 0 else entry * 0.002, entry * cfg.MAX_SL_PCT_OF_PRICE)
    tp_dist = sl_dist * 1.5   # R:R ~1.5:1
    if action == "BUY":
        sl_px = entry - sl_dist
        tp_px = entry + tp_dist
    else:
        sl_px = entry + sl_dist
        tp_px = entry - tp_dist

    res, used_sl, used_tp = send_order(
        broker_sym, action, lot, sl_px, tp_px,
        comment=f"{cfg.ORDER_COMMENT} - MANUAL",
    )
    if not res or res.retcode != RC_DONE:
        err = res.retcode if res else "None"
        if err == 10019:
            _no_money_until[login_int] = time.time() + cfg.NO_MONEY_BACKOFF_SEC
        return _fail(f"order_failed_{err}")

    position_id = None
    try:
        if res.deal:
            deals = mt5.history_deals_get(ticket=int(res.deal))
            if deals:
                position_id = int(deals[0].position_id)
    except Exception:
        pass
    ticket_str = str(position_id) if position_id else str(res.order)

    fill_tick = mt5.symbol_info_tick(broker_sym)
    fill_price = (fill_tick.ask if action == "BUY" else fill_tick.bid) if fill_tick else entry
    try:
        lic = db.query(License).filter(License.id == account.license_id).first()
        db.add(LiveTrade(
            license_key = lic.license_key if lic else None,
            mt5_login   = login_str, symbol = broker_sym,
            trade_type  = action, lot_size = float(lot),
            entry_price = float(fill_price),
            stop_loss   = float(used_sl) if used_sl is not None else None,
            take_profit = float(used_tp) if used_tp is not None else None,
            status      = "OPEN", mt5_ticket = ticket_str,
            opened_at   = datetime.now(timezone.utc), is_ai_trade = True,
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("manual LiveTrade persist failed (trade IS open): %s", e)

    _last_action_time[(login_int, symbol)] = time.time()
    return _done(ticket_str)


def process_manual_trades(db, account, acct_info, risk_mode) -> int:
    """
    Execute any PENDING manual (self-execution) trade requests for this account.
    Each request is fully isolated so one failure never blocks the others.
    """
    if account.license_id is None:
        return 0
    try:
        q = db.query(ManualTradeRequest).filter(
            ManualTradeRequest.license_id == account.license_id,
            ManualTradeRequest.status == "PENDING",
        )
        # PER-ACCOUNT isolation: if the request records which MT5 login it was
        # sent from, only execute it on THAT account — never on another account
        # sharing the same license.
        if hasattr(ManualTradeRequest, "mt5_login"):
            q = q.filter(ManualTradeRequest.mt5_login == str(account.login))
        reqs = q.order_by(ManualTradeRequest.id.asc()).limit(10).all()
    except Exception as e:
        logger.debug("manual-trade query failed: %s", e)
        return 0

    processed = 0
    for req in reqs:
        try:
            _execute_manual_trade(db, account, acct_info, risk_mode, req)
            processed += 1
        except Exception as e:
            try:
                req.status = "FAILED"
                req.error = str(e)[:200]
                req.processed_at = datetime.now(timezone.utc)
                db.commit()
            except Exception:
                db.rollback()
            logger.error("manual-trade #%s error: %s", getattr(req, "id", "?"), e)
    return processed


# ==============================================================================
# PER-USER PROCESSING
# ==============================================================================
def process_user(db: Session, account: ClientMT5Account,
                tradeable_signals: Dict[str, AISignal]) -> dict:
    """
    For one user:
      1. Switch to their MT5 account
      2. USER PANIC BUTTON — close_all_requested closes everything + stops AI
      3. Manage all open positions (exits) + persist scale state + live P&L
      4. Reconcile closed -> write to AITradeHistory (learning)
      5. Manual trades ALWAYS run (fixed: old code skipped them when no fresh
         signal existed)
      6. Build THIS USER'S signal set: shared signals + any recovery flip
         armed for this login only (fixed: flips used to be global)
      7. For each signal that's fresh, try to open a trade
    """
    outcome = {"attempted": False, "reason": "", "result": ""}

    try:
        login_int = int(account.login)
    except (ValueError, TypeError):
        outcome["reason"] = "invalid_login"; return outcome
    login_str = str(account.login)

    risk_mode = (account.risk_level or cfg.DEFAULT_MODE).lower()
    if risk_mode not in cfg.RISK_MODE:
        risk_mode = cfg.DEFAULT_MODE

    # No-money backoff
    if _no_money_until.get(login_int, 0) > time.time():
        outcome["reason"] = "no_money_backoff"; return outcome

    # Login quarantine — skip accounts that keep failing to connect.
    if _login_quarantine_until.get(login_int, 0) > time.time():
        outcome["reason"] = "login_quarantined"; return outcome

    # MT5 account switch (password decrypted just-in-time; plaintext legacy
    # rows pass through decrypt_secret unchanged)
    if not switch_account(login_int, decrypt_secret(account.password), account.server):
        _login_fail_count[login_int] += 1
        if _login_fail_count[login_int] >= cfg.LOGIN_QUARANTINE_AFTER:
            _login_quarantine_until[login_int] = time.time() + cfg.LOGIN_QUARANTINE_SEC
            logger.warning("⛔ login %s quarantined %dmin after %d failures",
                           login_int, cfg.LOGIN_QUARANTINE_SEC // 60,
                           _login_fail_count[login_int])
        outcome["reason"] = "mt5_login_failed"; return outcome
    _login_fail_count.pop(login_int, None)   # success — clear the failure streak
    info = mt5.account_info()
    if not info:
        outcome["reason"] = "no_account_info"; return outcome

    # ── Sync live balance/equity back to the DB so the dashboard shows the
    # REAL MT5 numbers (the API can't call MT5 itself on Linux).
    try:
        account.balance = float(info.balance or 0)
        account.equity = float(info.equity or 0)
        db.commit()
    except Exception:
        db.rollback()

    # ── USER PANIC BUTTON — close everything NOW, then stop the AI ───────────
    # The API sets close_all_requested=True; we honor it before anything else.
    # Reconciliation below records the outcomes on the following cycles.
    if getattr(account, "close_all_requested", False):
        n_closed = 0
        for p in [p for p in (mt5.positions_get() or []) if p.magic == cfg.MAGIC]:
            if _close_position(p, "USER_CLOSE_ALL") != "HOLD":
                n_closed += 1
        try:
            account.close_all_requested = False
            account.ai_auto_trade = False
            db.commit()
        except Exception:
            db.rollback()
        logger.info("🧯 CLOSE-ALL login=%s — closed %d position(s), AI stopped",
                    login_str, n_closed)

    # ── 1. Manage all open positions (exits run every cycle) ─────────────────
    open_positions = mt5.positions_get() or []
    ours = [p for p in open_positions if p.magic == cfg.MAGIC]

    # 1a. BASKET TP — group by symbol, close all when target hit
    by_symbol = defaultdict(list)
    for pos in ours:
        by_symbol[pos.symbol].append(pos)
    closed_tickets = set()
    for symbol, sym_positions in by_symbol.items():
        try:
            n_closed = manage_basket(sym_positions)
            if n_closed > 0:
                for p in sym_positions:
                    closed_tickets.add(p.ticket)
        except Exception as e:
            logger.error("Basket error %s: %s", symbol, e)

    # 1b. Individual position management (skip the ones closed in basket)
    for pos in ours:
        if pos.ticket in closed_tickets:
            continue
        try:
            manage_position(pos, login_int)
        except Exception as e:
            logger.error("Manage error %s: %s", pos.symbol, e)

    # ── 1c. LIVE P&L + SCALE-STATE PERSISTENCE ──────────────────────────────
    # Refresh LiveTrade.profit for every open row (dashboard live numbers) AND
    # persist scale_stage / peak_profit_001 so a worker restart can rebuild
    # the exact scale-out state (_rebuild_position_state) instead of re-firing
    # partials on positions that already scaled.
    pos_by_ticket = {str(p.ticket): p for p in ours}
    db_open_rows = db.query(LiveTrade).filter(
        LiveTrade.mt5_login == login_str,
        LiveTrade.status == "OPEN",
    ).all()
    db_open_tickets = {t.mt5_ticket for t in db_open_rows if t.mt5_ticket}

    for trade in db_open_rows:
        pos = pos_by_ticket.get(trade.mt5_ticket)
        if pos:
            try:
                trade.profit = float(pos.profit)
                # Persist scale-out state (restart-safe)
                stage = (2 if pos.ticket in _scale2_done
                         else 1 if pos.ticket in _scale1_done else 0)
                if hasattr(trade, "scale_stage") and (trade.scale_stage or 0) != stage:
                    trade.scale_stage = stage
                if hasattr(trade, "peak_profit_001"):
                    lm = float(pos.volume) / 0.01 if pos.volume else 1.0
                    per001 = float(pos.profit) / lm if lm > 0 else float(pos.profit)
                    if per001 > float(trade.peak_profit_001 or 0):
                        trade.peak_profit_001 = per001
            except Exception:
                pass

    # Recover orphans: MT5 has a position whose ticket isn't in any open LiveTrade
    try:
        lic_row = db.query(License).filter(License.id == account.license_id).first()
        lic_key = lic_row.license_key if lic_row else None
        for pos in ours:
            if str(pos.ticket) in db_open_tickets:
                continue
            try:
                action = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                recovery = LiveTrade(
                    license_key  = lic_key,
                    mt5_login    = login_str,
                    symbol       = pos.symbol,
                    trade_type   = action,
                    lot_size     = float(pos.volume),
                    entry_price  = float(pos.price_open),
                    stop_loss    = float(pos.sl) if pos.sl else None,
                    take_profit  = float(pos.tp) if pos.tp else None,
                    status       = "OPEN",
                    mt5_ticket   = str(pos.ticket),
                    profit       = float(pos.profit),
                    opened_at    = datetime.fromtimestamp(pos.time, tz=timezone.utc)
                                   if pos.time else datetime.now(timezone.utc),
                    is_ai_trade  = True,
                )
                db.add(recovery)
                logger.info("🔧 Recovered orphan position %s %s ticket=%s",
                           pos.symbol, action, pos.ticket)
            except Exception as e:
                logger.error("Recovery failed for ticket %s: %s", pos.ticket, e)
    except Exception as e:
        logger.error("Orphan scan failed: %s", e)

    try:
        db.commit()
    except Exception as e:
        logger.error("Live-P&L / recovery commit failed: %s", e)
        db.rollback()

    # ── 2. Reconcile closed positions -> learning loop ───────────────────────
    # Mark LiveTrade rows CLOSED when MT5 no longer has their position.
    # SAFETY: skip very fresh trades (<30s old).
    reconcile_cutoff = datetime.now(timezone.utc) - timedelta(seconds=30)
    db_open_trades = db.query(LiveTrade).filter(
        LiveTrade.mt5_login == login_str,
        LiveTrade.status == "OPEN",
    ).all()
    open_tickets = {str(p.ticket) for p in open_positions}
    for trade in db_open_trades:
        opened = trade.opened_at
        if opened is not None and opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        if opened is not None and opened > reconcile_cutoff:
            continue
        if trade.mt5_ticket and trade.mt5_ticket not in open_tickets:
            # GUARD: only treat as closed if MT5 HISTORY actually has a close
            # (OUT) deal for this ticket/position. If there's no close deal,
            # the position is still live under a shifted id — leave it OPEN.
            confirmed_closed, realized, close_price = _confirm_closed_and_profit(trade.mt5_ticket)
            if not confirmed_closed:
                continue

            # CENT-ACCOUNT SCALING — on a cent account the broker reports
            # profit x100; divide to true dollars BEFORE anything else.
            cent_div = _cent_account_divisor(info)
            realized = round(realized / cent_div, 2)
            if cent_div != 1.0:
                logger.info("🪙 cent account (%s) — scaled %s profit to $%.2f",
                            getattr(info, "currency", "?"), trade.symbol, realized)

            # ★ FIXED — NO MORE CLAMP-TO-ZERO. The value here is MT5's own
            # confirmed realized figure (close-deals-only + cent scaling).
            # The old clamp wrote real losses to history as "$0 BREAKEVEN",
            # which lied to the user AND poisoned the learning loop. We now
            # record the truth and keep a loud warning for forensic review of
            # unusually large values only.
            profit = realized
            try:
                lot_mult = float(trade.lot_size or 0.01) / 0.01
            except Exception:
                lot_mult = 1.0
            if abs(profit) > 60.0 * lot_mult:
                logger.warning("⚠️ large realized $%.2f on %s ticket %s — "
                               "recorded as-is; verify against MT5 history",
                               profit, trade.symbol, trade.mt5_ticket)

            reason = fetch_close_reason_from_mt5(trade.mt5_ticket) or infer_exit_reason(trade, profit)
            signal_obj = (db.query(AISignal).filter(AISignal.id == trade.ai_signal_id).first()
                         if trade.ai_signal_id else None)
            write_trade_outcome(db, trade, reason, profit, signal_obj, close_price=close_price)
            try:
                _partial_closed_tickets.discard(int(trade.mt5_ticket))
                _partial_closed_tier2.discard(int(trade.mt5_ticket))
                _scale1_done.discard(int(trade.mt5_ticket))
                _scale2_done.discard(int(trade.mt5_ticket))
                _runner_peak.pop(int(trade.mt5_ticket), None)
            except (ValueError, TypeError):
                pass

    # ── 3. ★ FIXED — do NOT return early when there are no fresh signals.
    # The old early-return here silently skipped MANUAL trades ("Send to MT5")
    # and recovery flips unless an unrelated fresh signal happened to exist.
    # Protection, manual trades and the per-user signal build all run below;
    # the no-signal exit happens only after those.

    # ── 4. Account protection ────────────────────────────────────────────────
    ok, why = account_protection_check(login_int, info)
    if not ok:
        outcome["reason"] = f"protection_{why}"
        return outcome

    # ── MANUAL TRADES (user-initiated, from the chart scanner) ───────────────
    try:
        process_manual_trades(db, account, info, risk_mode)
    except Exception as e:
        logger.error("manual trades error: %s", e)

    # ── COPIER (master-account trades from this client's admin) ──────────────
    # Runs here because the terminal is already logged into this account, so a
    # copied trade costs no extra login. Deliberately BEFORE the ai_auto_trade
    # gate: the copier is a separate product, and a user who switched the AI
    # off still expects their provider's trades.
    try:
        copier_tally = process_copier_executions(db, account, find_broker_symbol)
        if copier_tally:
            outcome["copier"] = copier_tally
    except Exception as e:
        logger.error("copier error: %s", e)   

    # ── AUTO-TRADE GATE ──────────────────────────────────────────────────────
    # If user has stopped AI, skip the entry-opening section. The position
    # management above STILL runs so existing positions are managed cleanly.
    if not account.ai_auto_trade:
        outcome["reason"] = "ai_stopped_by_user"
        return outcome

    # ── GLOBAL ENTRY GATE (operator kill-switch for AI entries) ─────────────
    # Placed AFTER manual trades so "Send to MT5" keeps working — the gate
    # blocks only AI-initiated entries. Management/exits above already ran.
    if not cfg.AI_ENTRIES_ENABLED:
        outcome["reason"] = "entries_globally_disabled"
        return outcome

    # ── 5. Build THIS USER'S signal set ─────────────────────────────────────
    # ★ FIXED — copy the shared dict, then inject any RECOVERY FLIP armed for
    # THIS login only. Previously flips were injected into the SHARED dict, so
    # one user's cut loss re-entered a trade on EVERY account.
    user_signals: Dict[str, AISignal] = dict(tradeable_signals)
    now_rf = time.time()
    for (rf_login, rf_cls), rec in list(_recovery_flips.items()):
        if now_rf - rec.get("ts", 0) >= 60:
            _recovery_flips.pop((rf_login, rf_cls), None)   # expire stale
            continue
        if rf_login != login_int:
            continue
        try:
            mt5.symbol_select(rec["symbol"], True)
            tick_r = mt5.symbol_info_tick(rec["symbol"])
            if tick_r:
                px_r = tick_r.ask if rec["dir"] == "BUY" else tick_r.bid
                rsig = SelfSignal(rec["symbol"], rec["dir"], 85, px_r,
                                  "recovery_flip_with_trend")
                user_signals[rec["symbol"]] = rsig
                logger.info("🔄 RECOVERY %s %s @ %.2f (login=%s, re-entering with trend)",
                            rec["dir"], rec["symbol"], float(px_r), login_int)
        except Exception as e:
            logger.debug("recovery inject failed: %s", e)
        _recovery_flips.pop((rf_login, rf_cls), None)       # consumed

    if not user_signals:
        outcome["reason"] = "no_signal"
        return outcome

    # ── 6. Try each signal — sorted by priority symbols first ────────────────
    sorted_signals = sorted(
        user_signals.items(),
        key=lambda kv: (0 if is_priority(kv[0]) else 1, -kv[1].id),
    )

    opens_this_cycle = 0
    last_reason = "no_signal"
    last_opened_result = None

    for symbol, signal in sorted_signals:

        # ── ACTIVATION CUTOFF — skip signals older than when this account went
        # active, so a freshly-connected / just-enabled user never inherits an
        # older in-flight signal. Normalize BOTH sides to aware-UTC first.
        cutoff = account.signal_cutoff_at
        sig_created = getattr(signal, "created_at", None)
        if cutoff is not None and sig_created is not None:
            try:
                if cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=timezone.utc)
                sc = sig_created if sig_created.tzinfo else sig_created.replace(tzinfo=timezone.utc)
                if sc < cutoff:
                    logger.info("⏭️ Skipping old signal #%s for login=%s (predates activation)",
                               signal.id, account.login)
                    continue
            except Exception as e:
                logger.debug("signal_cutoff compare failed: %s", e)

        if opens_this_cycle >= cfg.MAX_OPENS_PER_USER_PER_CYCLE:
            break
        result = try_open_trade(db, account, info, signal, risk_mode, {
            "attempted": False, "reason": "", "result": "",
        })
        if result.get("result") == "OPENED":
            opens_this_cycle += 1
            last_opened_result = result
            continue   # try the next symbol too
        if result.get("result") == "FAILED":
            outcome = result
            continue
        last_reason = result.get("reason", "no_signal")
        if os.environ.get("DEBUG_SIGNALS", "").lower() == "true":
            logger.info("↩️ %s %s -> %s", login_str, symbol, last_reason)

    if last_opened_result is not None:
        return last_opened_result
    if outcome.get("result") != "FAILED":
        outcome["reason"] = last_reason
    return outcome


# ==============================================================================
# TRADE OPEN
# ==============================================================================
def try_open_trade(db: Session, account, acct_info, signal: AISignal,
                  risk_mode: str, outcome: dict) -> dict:
    """Attempt to open a trade for one user based on the signal."""
    login_int = int(account.login)
    login_str = str(account.login)

    # Signal confidence & basic checks
    action = (signal.action or "").upper()
    if action not in ("BUY", "SELL"):
        outcome["reason"] = "invalid_action"; return outcome

    symbol = (signal.symbol or "").upper().replace(".A", "").replace(".M", "")
    priority = is_priority(symbol)
    min_conf = cfg.MIN_CONFIDENCE_PRIORITY if priority else cfg.MIN_CONFIDENCE_STANDARD
    if (signal.confidence or 0) < min_conf:
        outcome["reason"] = f"low_confidence_{signal.confidence}"; return outcome

    # User symbol settings
    if account.license_id is None:
        outcome["reason"] = "no_license"; return outcome
    setting = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == account.license_id,
        ClientSymbolSetting.symbol_name == symbol,
    ).first()
    if not setting or not setting.enabled:
        outcome["reason"] = f"symbol_{symbol}_not_enabled"; return outcome

    direction = parse_direction(setting)
    if not direction_allows(direction, action):
        outcome["reason"] = f"direction_{direction}_blocks_{action}"; return outcome

    # ── LOT REQUIRED — the user MUST have set a lot for this symbol.
    # No lot -> no trade. (The UI now surfaces needs_lot so this state is
    # visible instead of silently skipping.)
    if parse_user_lot(setting) is None:
        outcome["reason"] = f"symbol_{symbol}_no_lot_set"; return outcome

    # Broker symbol resolution
    broker_sym = find_broker_symbol(symbol)
    if not broker_sym:
        outcome["reason"] = f"broker_symbol_not_found_{symbol}"; return outcome
    mt5.symbol_select(broker_sym, True)

    # ── MARKET DATA VALIDATION — refuse to trade blind ──────────────────────
    # If we can't see live price + recent candles for this symbol on THIS
    # broker, we are NOT allowed to open a position. Trading blind is how
    # accounts die.
    vtick = mt5.symbol_info_tick(broker_sym)
    vrates = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M1, 0, 5)
    if (not vtick or vtick.bid <= 0 or vtick.ask <= 0
            or vrates is None or len(vrates) < 3):
        outcome["reason"] = f"no_market_data_{broker_sym}"; return outcome

    sym_class = classify_symbol(broker_sym)

    # Cooldown per (login, symbol)
    cooldown = cfg.FLIP_COOLDOWN_PRIORITY if priority else cfg.FLIP_COOLDOWN_STANDARD
    last_t = _last_action_time.get((login_int, symbol), 0)
    if time.time() - last_t < cooldown:
        outcome["reason"] = "cooldown"; return outcome

    # ── POSITION ANALYSIS (hedge + signal-flip handling) ────────────────────
    # Same-direction: allowed up to user's max_trades (stacking OK).
    # Opposite-direction: signal flipped → CLOSE the old position(s) first,
    # then open the new direction. Never hedge (buy+sell same class = paying
    # spread twice to go nowhere).
    all_positions = mt5.positions_get() or []
    same_dir = []
    opp_dir = []
    for p in all_positions:
        if p.magic != cfg.MAGIC:
            continue
        if classify_symbol(p.symbol) != sym_class:
            continue
        p_is_buy = (p.type == mt5.POSITION_TYPE_BUY)
        if p_is_buy == (action == "BUY"):
            same_dir.append(p)
        else:
            opp_dir.append(p)

    if opp_dir:
        # Signal flipped against existing position(s) on this symbol class.
        # A pro scalper doesn't sit through the reversal — close and flip.
        # Rate-limited so choppy signals can't churn flips back and forth.
        last_flip = _last_flip_time.get((login_int, sym_class), 0)
        if time.time() - last_flip < cfg.REVERSAL_FLIP_COOLDOWN_SEC:
            outcome["reason"] = "flip_cooldown_active"; return outcome
        closed_any = False
        for p in opp_dir:
            if _close_position(p, f"SIGNAL_FLIP_TO_{action}") != "HOLD":
                closed_any = True
        if closed_any:
            _last_flip_time[(login_int, sym_class)] = time.time()
            logger.info("🔁 SIGNAL FLIP %s: closed %d opposite position(s), opening %s",
                       sym_class, len(opp_dir), action)
            # ★ was 0.5s — shortened: this sleep runs INSIDE MT5_LOCK, so every
            # 100ms here delays every other account on the shard. 0.2s is
            # enough for the broker to register the closes before the open.
            time.sleep(0.2)

    # Max same-direction trades (user setting bounded by hard cap)
    user_max = parse_max_trades(setting, risk_mode)
    hard_cap = cfg.HARD_MAX_SAME_DIR_PER_SYMBOL.get(
        sym_class, cfg.HARD_MAX_SAME_DIR_PER_SYMBOL["OTHER"])
    max_trades = min(user_max, hard_cap)
    if len(same_dir) >= max_trades:
        outcome["reason"] = f"max_trades_reached_{len(same_dir)}_of_{max_trades}"
        return outcome

    # ── VOLUME CAP: total same-direction lots must fit mode's budget ────────
    # max total volume = max_trades x per-trade lot. Stops a runaway stack
    # (e.g. partial-close remainders) from exceeding what the mode intends.
    per_trade_lot = parse_user_lot(setting) or cfg.LOT_MIN
    max_total_vol = round(max_trades * per_trade_lot, 2)
    cur_vol = round(sum(float(p.volume) for p in same_dir), 2)
    if cur_vol + per_trade_lot > max_total_vol + 1e-9:
        outcome["reason"] = f"vol_cap_{cur_vol}_of_{max_total_vol}"
        return outcome

    # Total positions hard cap
    ours_total = [p for p in all_positions if p.magic == cfg.MAGIC]
    if len(ours_total) >= cfg.HARD_MAX_TOTAL_POSITIONS:
        outcome["reason"] = f"hard_cap_total_{len(ours_total)}"
        return outcome

    # ── ANTI-BLIND-ENTRY GATES ──────────────────────────────────────────────
    gate_block = check_entry_gates(login_int, symbol, action, same_dir)
    if gate_block:
        outcome["reason"] = gate_block
        return outcome

    # Spread quality
    ok_spread, cur_spread, avg_spread = spread_is_acceptable(broker_sym)
    if not ok_spread:
        outcome["reason"] = f"spread_spike_{cur_spread}_avg_{avg_spread:.1f}"
        return outcome

    # Lot sizing — user's exact lot (with margin-budget backstop)
    user_lot = parse_user_lot(setting)
    learn_mult = learning_lot_multiplier(symbol, getattr(signal, "entry_quality", None))
    lot, lot_source = compute_lot(
        broker_sym, user_lot, risk_mode,
        learning_mult=learn_mult,
        account_balance=float(acct_info.balance or 1000.0),
    )
    if not lot or lot <= 0:
        outcome["reason"] = lot_source or "lot_zero_unaffordable"
        return outcome

    # ── NOTIONAL EXPOSURE CAP (balance-aware leverage guard) ────────────────
    try:
        balance = float(acct_info.balance or 1.0)
        existing_notional = sum(float(p.volume) * float(p.price_open)
                               for p in ours_total)
        cand_tick = mt5.symbol_info_tick(broker_sym)
        cand_price = float(cand_tick.ask) if cand_tick else 0.0
        total_after = existing_notional + lot * cand_price
        if balance < 500:
            lev_cap = 10.0
        elif balance < 2000:
            lev_cap = 20.0
        else:
            lev_cap = cfg.HARD_MAX_NOTIONAL_PER_BALANCE
        if total_after > lev_cap * balance:
            outcome["reason"] = f"notional_cap_{total_after:.0f}_gt_{lev_cap*balance:.0f}"
            return outcome
    except Exception:
        pass

    # ── SCALP BRACKET — USD-based TP with adaptive spread widening ──────────
    info_s = mt5.symbol_info(broker_sym)
    tick_s = mt5.symbol_info_tick(broker_sym)
    if not info_s or not tick_s:
        outcome["reason"] = "no_symbol_info"
        return outcome

    entry_ref = tick_s.ask if action == "BUY" else tick_s.bid

    # USD target per 0.01 lot for this class, scaled by actual lot
    usd_target_001 = cfg.SCALP_TARGET_USD_PER_001.get(sym_class, 1.0)
    lot_mult = lot / 0.01

    # Convert USD target -> price distance using broker tick economics
    scalp_distance = 0.0
    try:
        if info_s.trade_tick_value and info_s.trade_tick_size and lot > 0:
            usd_to_price = info_s.trade_tick_size / (info_s.trade_tick_value * lot)
            scalp_distance = (usd_target_001 * lot_mult) * usd_to_price
    except Exception:
        scalp_distance = 0.0
    if scalp_distance <= 0:
        # Fallback: percentage of price by class
        pct = {"GOLD": 0.0004, "BTC": 0.0008, "ETH": 0.0008}.get(sym_class, 0.0005)
        scalp_distance = entry_ref * pct

    # Adaptive spread widening: if spread eats too much of the TP, widen the
    # TP so spread <= SCALP_TARGET_SPREAD_PCT of it (up to a cap).
    cur_spread_px = abs(tick_s.ask - tick_s.bid)
    if cur_spread_px > 0 and scalp_distance > 0:
        spread_frac = cur_spread_px / scalp_distance
        if spread_frac > cfg.SCALP_TARGET_SPREAD_PCT:
            widen = min(spread_frac / cfg.SCALP_TARGET_SPREAD_PCT,
                        cfg.SCALP_MAX_WIDEN_MULT)
            scalp_distance *= widen

    # ── THE SECOND BRAIN — confirm or refuse this entry ─────────────────────
    sig_entry = float(getattr(signal, "entry_price", 0) or 0)
    take, brain_reason = scalper_brain_decision(
        broker_sym, action, signal, scalp_distance, sig_entry,
        getattr(signal, "created_at", None),
    )
    if not take:
        outcome["reason"] = brain_reason
        return outcome

    # ── ENTRY SLIPPAGE GUARD (vs signal price, in ATR units) ────────────────
    atr_e = calculate_atr(broker_sym, period=14)
    if sig_entry > 0 and atr_e > 0:
        drift = (entry_ref - sig_entry) if action == "BUY" else (sig_entry - entry_ref)
        # Positive drift = price already ran the signal's way (chasing)
        if drift > atr_e * cfg.MAX_ENTRY_SLIPPAGE_ATR:
            outcome["reason"] = f"entry_drifted_{drift/atr_e:.1f}atr"
            return outcome

    # ── STOPS ───────────────────────────────────────────────────────────────
    # Default scalp bracket: TP = scalp_distance, SL = 0.7x TP (R:R ~1.4:1 in
    # our favour), hard-capped at MAX_SL_PCT_OF_PRICE.
    # GOLD TREND-SCALP overrides with an ATR bracket (room for pullbacks).
    use_gold_trend = (sym_class == "GOLD" and cfg.GOLD_TREND_SCALP
                      and getattr(signal, "id", -1) == -1)
    if use_gold_trend and atr_e > 0:
        sl_dist = min(atr_e * cfg.GOLD_TREND_SL_ATR,
                      entry_ref * cfg.MAX_SL_PCT_OF_PRICE)
        tp_dist = atr_e * cfg.GOLD_TREND_TP_ATR
    else:
        sl_dist = min(scalp_distance * cfg.SCALP_SL_MULTIPLIER,
                      entry_ref * cfg.MAX_SL_PCT_OF_PRICE)
        tp_dist = scalp_distance

    if getattr(signal, "stop_loss", None) and getattr(signal, "id", -1) != -1:
        # Watcher-provided stops (forex path): bound them to sane distance
        w_sl = float(signal.stop_loss)
        w_dist = abs(entry_ref - w_sl)
        max_dist = entry_ref * cfg.MAX_SL_PCT_OF_PRICE
        if 0 < w_dist <= max_dist:
            sl_dist = w_dist
        if getattr(signal, "take_profit", None):
            w_tp = float(signal.take_profit)
            w_tpd = abs(w_tp - entry_ref)
            if w_tpd > 0:
                tp_dist = w_tpd

    if action == "BUY":
        sl_px = entry_ref - sl_dist
        tp_px = entry_ref + tp_dist
    else:
        sl_px = entry_ref + sl_dist
        tp_px = entry_ref - tp_dist

    # ── SEND ────────────────────────────────────────────────────────────────
    res, used_sl, used_tp = send_order(broker_sym, action, lot, sl_px, tp_px)
    if not res or res.retcode != RC_DONE:
        err = res.retcode if res else "None"
        if err == 10019:  # no money
            _no_money_until[login_int] = time.time() + cfg.NO_MONEY_BACKOFF_SEC
        outcome["attempted"] = True
        outcome["result"] = "FAILED"
        outcome["reason"] = f"order_failed_{err}"
        logger.warning("❌ OPEN FAILED %s %s login=%s err=%s",
                      action, broker_sym, login_str, err)
        return outcome

    # Resolve POSITION id (not order id) for reliable reconciliation
    position_id = None
    try:
        if res.deal:
            deals = mt5.history_deals_get(ticket=int(res.deal))
            if deals:
                position_id = int(deals[0].position_id)
    except Exception:
        pass
    ticket_str = str(position_id) if position_id else str(res.order)

    fill_tick = mt5.symbol_info_tick(broker_sym)
    fill_price = (fill_tick.ask if action == "BUY" else fill_tick.bid) if fill_tick else entry_ref

    # Persist LiveTrade (resilient — trade IS open even if this write fails)
    try:
        lic = db.query(License).filter(License.id == account.license_id).first()
        db.add(LiveTrade(
            license_key = lic.license_key if lic else None,
            mt5_login   = login_str,
            symbol      = broker_sym,
            trade_type  = action,
            lot_size    = float(lot),
            entry_price = float(fill_price),
            stop_loss   = float(used_sl) if used_sl is not None else None,
            take_profit = float(used_tp) if used_tp is not None else None,
            status      = "OPEN",
            mt5_ticket  = ticket_str,
            ai_signal_id = signal.id if getattr(signal, "id", -1) != -1 else None,
            opened_at   = datetime.now(timezone.utc),
            is_ai_trade = True,
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("LiveTrade persist failed (trade IS open): %s", e)

    _last_action_time[(login_int, symbol)] = time.time()
    outcome["attempted"] = True
    outcome["result"] = "OPENED"
    outcome["reason"] = f"opened_{action}_{lot}"
    logger.info("✅ OPENED %s %s %.2f @ %.5f | sl=%.5f tp=%.5f | login=%s | lot_src=%s",
               action, broker_sym, lot, fill_price,
               used_sl or 0, used_tp or 0, login_str, lot_source)
    return outcome


# ==============================================================================
# SELF-SUFFICIENT SIGNAL ENGINE (XAUUSD & BTCUSD)
# ==============================================================================
class SelfSignal:
    """Lightweight signal object for worker's own analysis (mirrors AISignal
    attributes the pipeline reads). id=-1 marks it as self-generated."""
    def __init__(self, symbol, action, confidence, entry_price, quality):
        self.id = -1
        self.symbol = symbol
        self.action = action
        self.confidence = confidence
        self.entry_price = entry_price
        self.stop_loss = None
        self.take_profit = None
        self.entry_quality = quality
        self.trend = None
        self.structure = None
        self.created_at = datetime.now(timezone.utc)


def _ema(vals, period):
    if not vals:
        return 0.0
    if len(vals) < period:
        return sum(vals) / len(vals)
    k = 2 / (period + 1)
    e = sum(vals[:period]) / period
    for v in vals[period:]:
        e = v * k + e * (1 - k)
    return e


def analyze_for_self(broker_sym: str) -> Optional["SelfSignal"]:
    """
    The worker's OWN read on XAUUSD/BTCUSD — no watcher needed.

    GOLD (GOLD_TREND_SCALP): trade WITH the continuous M5 trend.
      Entry triggers (either):
        • IGNITION: fresh M1 momentum burst in the trend direction
        • PULLBACK-GO: price pulled back to M5 EMA21 zone and resumed
    BTC: momentum-continuation read (M5 trend + M1 thrust agree).
    Mean-reversion fade is kept behind MEAN_REVERSION_XAU for comparison.
    """
    try:
        m1 = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M1, 0, 30)
        m5 = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M5, 0, 60)
        if m1 is None or m5 is None or len(m1) < 20 or len(m5) < 40:
            return None
        tick = mt5.symbol_info_tick(broker_sym)
        if not tick:
            return None

        sym_class = classify_symbol(broker_sym)
        atr15 = calculate_atr(broker_sym, period=14)
        if atr15 <= 0:
            return None

        c5 = [r["close"] for r in m5[:-1]]
        ema9_5  = _ema(c5, 9)
        ema21_5 = _ema(c5, 21)
        ema50_5 = _ema(c5, 50)
        up_trend   = ema9_5 > ema21_5 > ema50_5
        down_trend = ema9_5 < ema21_5 < ema50_5

        closed1 = m1[:-1]
        last1 = closed1[-1]
        last_body = last1["close"] - last1["open"]
        avg_body = (sum(abs(r["close"] - r["open"]) for r in closed1[-15:]) / 15) or 1e-9

        price = tick.bid
        # ── GOLD: TREND-SCALP ───────────────────────────────────────────────
        if sym_class == "GOLD" and cfg.GOLD_TREND_SCALP:
            if not (up_trend or down_trend):
                return None
            want = "BUY" if up_trend else "SELL"

            # Trigger A — IGNITION: strong fresh M1 candle WITH the trend
            ignition = (abs(last_body) > atr15 * cfg.GOLD_IGNITION_ATR
                        and ((want == "BUY" and last_body > 0)
                             or (want == "SELL" and last_body < 0))
                        and abs(last_body) > avg_body * 1.5)

            # Trigger B — PULLBACK-GO: price near M5 EMA21 and resuming
            near_ema = abs(price - ema21_5) < atr15 * 0.35
            resuming = ((want == "BUY" and last_body > 0)
                        or (want == "SELL" and last_body < 0))
            pullback_go = near_ema and resuming

            if not (ignition or pullback_go):
                return None
            quality = "gold_ignition" if ignition else "gold_pullback_go"
            px = tick.ask if want == "BUY" else tick.bid
            return SelfSignal(broker_sym, want, 80, px, quality)

        # ── GOLD: legacy mean-reversion fade (toggle) ───────────────────────
        if sym_class == "GOLD" and cfg.MEAN_REVERSION_XAU:
            push = price - c5[-6]
            if abs(push) < atr15 * cfg.MEANREV_PUSH_ATR:
                return None
            want = "SELL" if push > 0 else "BUY"
            if cfg.MEANREV_TREND_FILTER:
                if want == "SELL" and up_trend:
                    return None
                if want == "BUY" and down_trend:
                    return None
            px = tick.ask if want == "BUY" else tick.bid
            return SelfSignal(broker_sym, want, 72, px, "meanrev_fade")

        # ── BTC: momentum continuation ──────────────────────────────────────
        if sym_class == "BTC":
            if not (up_trend or down_trend):
                return None
            want = "BUY" if up_trend else "SELL"
            bodies3 = [r["close"] - r["open"] for r in closed1[-3:]]
            thrust = (all(b > 0 for b in bodies3) if want == "BUY"
                      else all(b < 0 for b in bodies3))
            strong = abs(last_body) > avg_body * 1.3
            if not (thrust and strong):
                return None
            px = tick.ask if want == "BUY" else tick.bid
            return SelfSignal(broker_sym, want, 78, px, "btc_momentum")

        return None
    except Exception as e:
        logger.debug("analyze_for_self %s failed: %s", broker_sym, e)
        return None


# ==============================================================================
# ★ STARTUP STATE REBUILD — restore scale-out state from LiveTrade rows
# ==============================================================================
def _rebuild_position_state(db: Session) -> None:
    """
    Rebuild the in-memory scale-out sets from the persisted columns so a
    worker restart never re-fires SCALE1 on a runner or forgets its peak.
    (process_user's section 1c writes scale_stage/peak_profit_001 every
    cycle; this is the read-side at boot.)
    """
    try:
        rows = db.query(LiveTrade).filter(LiveTrade.status == "OPEN").all()
        restored = 0
        for t in rows:
            try:
                tk = int(t.mt5_ticket)
            except (ValueError, TypeError):
                continue
            stage = int(getattr(t, "scale_stage", 0) or 0)
            if stage >= 1:
                _scale1_done.add(tk)
            if stage >= 2:
                _scale2_done.add(tk)
                pk = float(getattr(t, "peak_profit_001", 0) or 0)
                if pk > 0:
                    _runner_peak[tk] = pk
            if stage >= 1:
                restored += 1
        if restored:
            logger.info("🔁 Restored scale-out state for %d open position(s) "
                        "(scale1=%d scale2=%d)",
                        restored, len(_scale1_done), len(_scale2_done))
    except Exception as e:
        logger.warning("position-state rebuild failed (continuing fresh): %s", e)


# ==============================================================================
# MAIN LOOP
# ==============================================================================
def main_loop():
    global _startup_high_water_mark
    logger.info("═" * 70)
    logger.info("  NOLIMITZ AI — EXECUTION WORKER (THE TRADER) — FIX PACK")
    logger.info("  shard %d of %d | loop=%ds | max_users/cycle=%d",
               cfg.SHARD_INDEX, cfg.SHARD_TOTAL, cfg.LOOP_DELAY, cfg.MAX_USERS_PER_CYCLE)
    if cfg.WORKER_NAME:
        logger.info("  worker identity: %s", cfg.WORKER_NAME)
    if cfg.AI_ONLY_LOGINS:
        logger.info("  🎯 ONLY these logins: %s", ", ".join(sorted(cfg.AI_ONLY_LOGINS)))
    if cfg.AI_SKIP_LOGINS:
        logger.info("  ⛔ SKIPPING logins: %s", ", ".join(sorted(cfg.AI_SKIP_LOGINS)))
    if not cfg.AI_ENTRIES_ENABLED:
        logger.warning("  🚫 AI ENTRIES GLOBALLY DISABLED (AI_ENTRIES_ENABLED=false)")
        logger.warning("     Managing existing positions + manual trades ONLY.")
        logger.warning("     No strategy has passed validation; entries stay off")
        logger.warning("     until one earns a demo forward-test pass.")
    logger.info("═" * 70)

    if not init_mt5():
        return

    # Startup: learning stats + signal high-water mark + persisted scale state
    with SessionLocal() as db:
        refresh_learning(db)
        try:
            top = db.query(AISignal).order_by(AISignal.id.desc()).first()
            _startup_high_water_mark = top.id if top else 0
            logger.info("📌 Signal high-water mark at startup: %d", _startup_high_water_mark)
        except Exception:
            _startup_high_water_mark = 0
        # ★ Rebuild scale-out state (restart-proof winner management)
        _rebuild_position_state(db)

    cycle_num = 0
    while not _shutdown:
        cycle_start = time.time()
        cycle_num += 1
        db = None
        outcomes = defaultdict(int)
        process_list = []
        process_list = []
        try:
            db = SessionLocal()

            # Learning refresh (every 30 min)
            if time.time() - _learning_last_refresh > cfg.LEARNING_REFRESH_SEC:
                refresh_learning(db)

            # ★ Heartbeat to DB (~once a minute at a 3s loop) so /engine-status
            # can show this shard as online with its cycle number.
            if cycle_num % 20 == 1:
                _beat(db, cfg.WORKER_NAME or f"trader-shard{cfg.SHARD_INDEX}",
                      f"cycle={cycle_num}" +
                      ("" if cfg.AI_ENTRIES_ENABLED else " | ENTRIES OFF"))

            # ── Gather fresh signals (dedup: latest per symbol) ─────────────
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=cfg.SIGNAL_MAX_AGE_SECONDS)
            recent_signals = db.query(AISignal).filter(
                or_(AISignal.created_at >= cutoff,
                    AISignal.id > _startup_high_water_mark)
            ).order_by(AISignal.id.desc()).limit(50).all()

            tradeable_signals: Dict[str, AISignal] = {}
            for sig in recent_signals:
                sym = (sig.symbol or "").upper()
                if not sym:
                    continue
                # created_at may be NULL on some rows — the high-water-mark
                # filter above already guarantees "new since startup".
                if sig.created_at is not None:
                    sc = sig.created_at if sig.created_at.tzinfo else \
                         sig.created_at.replace(tzinfo=timezone.utc)
                    if sc < cutoff:
                        continue
                if sym not in tradeable_signals:
                    tradeable_signals[sym] = sig

            # ── XAUUSD & BTCUSD SELF-SUFFICIENT MODE ─────────────────────────
            # The worker analyzes gold & bitcoin ITSELF every cycle — no
            # watcher dependency for the two priority markets. Watcher signals
            # for these classes are dropped (self mode owns them).
            # ★ The recovery-flip injection that used to live here was REMOVED:
            # it pushed one user's recovery signal into this SHARED dict, so a
            # single user's cut loss re-entered a trade on EVERY account.
            # Recovery flips are now injected PER-LOGIN inside process_user.
            try:
                for prio in [s.strip() for s in os.environ.get("SELF_SYMBOLS", "XAUUSD,BTCUSD").split(",") if s.strip()]:
                    bsym = find_broker_symbol(prio)
                    if not bsym:
                        continue
                    prio_cls = classify_symbol(bsym)

                    # Watcher signals for these symbols are IGNORED (self mode owns them)
                    for _k in [k for k, s in list(tradeable_signals.items())
                               if classify_symbol(k) == prio_cls]:
                        tradeable_signals.pop(_k, None)

                    self_sig = analyze_for_self(bsym)
                    if self_sig:
                        tradeable_signals[bsym] = self_sig
            except Exception as e:
                logger.error("Self-analysis error: %s", e)

            # ── Load accounts (verified + active), sharded by login ─────────
            accounts_q = db.query(ClientMT5Account).filter(
                ClientMT5Account.is_verified == True,
                ClientMT5Account.is_active == True,
            ).all()
            accounts = []
            for a in accounts_q:
                try:
                    lg = int(a.login)
                except (ValueError, TypeError):
                    continue
                if cfg.SHARD_TOTAL > 1 and (lg % cfg.SHARD_TOTAL) != cfg.SHARD_INDEX:
                    continue
                # Two-track filters: a demo worker takes ONLY its logins; the
                # client worker SKIPs them — so two workers on one DB can
                # never double-process (and double-trade) the same account.
                if cfg.AI_ONLY_LOGINS and str(a.login) not in cfg.AI_ONLY_LOGINS:
                    continue
                if str(a.login) in cfg.AI_SKIP_LOGINS:
                    continue
                accounts.append(a)

            if not accounts:
                if cycle_num % 20 == 0:
                    logger.info("💤 No verified accounts on shard %d", cfg.SHARD_INDEX)
                continue

            # ── FAST-PATH PARTITION ─────────────────────────────────────────
            # Openers: accounts with AI enabled (may open new trades), pending
            # manual requests, or a close-all request. Managers: accounts with
            # open positions on OUR magic (must be managed even if AI is off).
            # Idle accounts are skipped except on the periodic full sweep.
            full_sweep = (cycle_num % cfg.FULL_SWEEP_EVERY == 0)
            if full_sweep:
                process_list = accounts
            else:
                enabled_lids = {a.license_id for a in accounts if a.ai_auto_trade}
                try:
                    manual_lids = {
                        r.license_id for r in db.query(ManualTradeRequest).filter(
                            ManualTradeRequest.status == "PENDING"
                        ).all()
                    }
                except Exception:
                    manual_lids = set()
                open_logins = set()
                try:
                    open_rows = db.query(LiveTrade).filter(
                        LiveTrade.status == "OPEN"
                    ).all()
                    open_logins = {str(t.mt5_login) for t in open_rows}
                except Exception:
                    pass
                openers = [a for a in accounts
                           if a.ai_auto_trade
                           or a.license_id in manual_lids
                           or getattr(a, "close_all_requested", False)]   # ★ panic served fast
                managers = [a for a in accounts
                            if str(a.login) in open_logins
                            and a not in openers]
                process_list = openers + managers

            process_list = process_list[:cfg.MAX_USERS_PER_CYCLE]

            # ── Process each user under the terminal lock ───────────────────
            for account in process_list:
                if _shutdown:
                    break
                with MT5_LOCK:
                    try:
                        result = process_user(db, account, tradeable_signals)
                        outcomes[result.get("reason") or result.get("result") or "?"] += 1
                    except Exception as e:
                        logger.error("process_user error login=%s: %s",
                                    account.login, e, exc_info=True)
                        outcomes["exception"] += 1
                        try:
                            db.rollback()
                        except Exception:
                            pass

            # Drop expired market-data cache entries so it can't grow unbounded
            # (cheap — only a handful of symbols are ever cached).
            _mkt_cache_sweep()

            # Cycle summary (only when something notable happened)
            notable = {k: v for k, v in outcomes.items()
                       if k not in ("no_signal", "ai_stopped_by_user")}
            if notable or cycle_num % 20 == 0:
                logger.info("🔄 Cycle %d | %d accounts | signals=%d | %s",
                           cycle_num, len(process_list), len(tradeable_signals),
                           dict(outcomes))

        except Exception as e:
            logger.error("Cycle error: %s", e, exc_info=True)
            # Terminal-level failures: try to recover connection
            if not mt5.terminal_info():
                _recover_terminal()
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

        elapsed = time.time() - cycle_start
        logger.info("TIMING | cycle %d | %.1fs total | %d accounts | %.2fs each",
                    cycle_num, elapsed, len(process_list),
                    elapsed / max(len(process_list), 1))
        logger.info("TIMING | cycle %d | %.1fs total | %d accounts | %.2fs each",
                    cycle_num, elapsed, len(process_list),
                    elapsed / max(len(process_list), 1))
        time.sleep(max(0.5, cfg.LOOP_DELAY - elapsed))

    logger.info("✅ Trader shut down cleanly")
    mt5.shutdown()


if __name__ == "__main__":
    main_loop()
