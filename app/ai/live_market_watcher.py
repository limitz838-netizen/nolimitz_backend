"""
================================================================================
  LIVE MARKET WATCHER v6 — PRO DAY-TRADER EDITION (Nolimitz Ai)
================================================================================

  ✓ XAUUSD/BTCUSD priority thresholds (LEAN=42, DISPLAY=50, SIGNAL=65)
  ✓ Standard thresholds for forex (LEAN=48, DISPLAY=58, SIGNAL=70)
  ✓ Wide-spread no longer suppresses dashboard display (only signal saving)
  ✓ BTCUSD broker symbol variants (BTCUSD/BTC/BITCOIN/BTCUSDT)
  ✓ GBPUSD decide_action fix (no more "stuck on WAIT")
  ✓ Min candles 40 (was 60) — handles brokers with limited BTC history
================================================================================
"""

import os
import time
import signal as sys_signal
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple

import MetaTrader5 as mt5
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ai.models.ai_market_state import AIMarketState
from app.models import AISignal, AISymbol
from app.ai.models.ai_trade_history import AITradeHistory


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("watcher_v6")


# ==============================================================================
# CONFIG
# ==============================================================================
class Config:
    BRAND = "Nolimitz Ai"

    PRIORITY_LEAN    = 42
    PRIORITY_DISPLAY = 50
    PRIORITY_SIGNAL  = 65

    STANDARD_LEAN    = 48
    STANDARD_DISPLAY = 58
    STANDARD_SIGNAL  = 70

    MIN_RR_RATIO = float(os.environ.get("MIN_RR_RATIO", "1.5"))
    # Spread limits per asset class (pips). Generous — the watcher's job is to
    # find setups, not pre-judge execution. The WORKER does final spread check
    # at order send time using real-time tick.
    MAX_SPREAD_PIPS_FOR_SIGNAL = {
        "GOLD":  150.0,
        "BTC":   5000.0,  # FBS-Demo reports up to 2500p; legit BTC brokers ~10-100p
        "ETH":   500.0,
        "INDEX": 100.0,
        "OIL":   80.0,
        "JPY":   30.0,
        "FOREX": 25.0,
        "OTHER": 40.0,
    }

    SIGNAL_COOLDOWN = int(os.environ.get("SIGNAL_COOLDOWN", "120"))
    CORR_COOLDOWN   = int(os.environ.get("CORR_COOLDOWN",   "300"))

    SCAN_INTERVAL   = int(os.environ.get("SCAN_INTERVAL", "20"))
    LTF_CANDLES     = int(os.environ.get("LTF_CANDLES", "300"))
    MTF_CANDLES     = int(os.environ.get("MTF_CANDLES", "200"))
    HTF_CANDLES     = int(os.environ.get("HTF_CANDLES", "150"))
    MIN_CANDLES_REQUIRED = int(os.environ.get("MIN_CANDLES_REQUIRED", "40"))

    NEWS_PAUSE_MINS = int(os.environ.get("NEWS_PAUSE_MINS", "15"))
    HIGH_IMPACT_NEWS = [
        (8,30),(9,0),(13,30),(14,0),(15,0),(15,30),(16,0),(19,0),(20,0),(21,30),
    ]

    CORRELATED_PAIRS = {
        "EURUSD": ["GBPUSD", "AUDUSD", "NZDUSD"],
        "GBPUSD": ["EURUSD", "AUDUSD"],
        "AUDUSD": ["EURUSD", "GBPUSD", "NZDUSD"],
        "NZDUSD": ["EURUSD", "AUDUSD"],
        "USDJPY": ["USDCAD", "USDCHF"],
    }

cfg = Config()


def is_priority(symbol: str) -> bool:
    sym = symbol.upper()
    return ("XAU" in sym or "GOLD" in sym or "BTC" in sym or "BITCOIN" in sym)


def classify_symbol(symbol: str) -> str:
    """Classify symbol for per-class settings (spread limits etc)."""
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


def get_spread_limit(symbol: str) -> float:
    """Returns max acceptable spread in pips for this symbol's asset class."""
    return cfg.MAX_SPREAD_PIPS_FOR_SIGNAL.get(classify_symbol(symbol), 25.0)


def get_thresholds(symbol: str) -> Tuple[int, int, int]:
    if is_priority(symbol):
        return cfg.PRIORITY_LEAN, cfg.PRIORITY_DISPLAY, cfg.PRIORITY_SIGNAL
    return cfg.STANDARD_LEAN, cfg.STANDARD_DISPLAY, cfg.STANDARD_SIGNAL


# ==============================================================================
# SHUTDOWN
# ==============================================================================
_shutdown = False

def _handle_shutdown(signum, frame):
    global _shutdown
    logger.info("🛑 Shutdown — completing cycle cleanly")
    _shutdown = True

sys_signal.signal(sys_signal.SIGINT, _handle_shutdown)
sys_signal.signal(sys_signal.SIGTERM, _handle_shutdown)


# ==============================================================================
# PERFORMANCE TRACKING
# ==============================================================================
SYMBOL_PERFORMANCE = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
SETUP_PERFORMANCE  = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})


def load_performance_from_db(db: Session) -> None:
    try:
        rows = db.query(AITradeHistory).all()
        for row in rows:
            sym = getattr(row, "symbol", None)
            setup = getattr(row, "setup_type", None)
            outcome = getattr(row, "result", None) or getattr(row, "status", None)
            if sym and outcome in ("WIN", "LOSS"):
                SYMBOL_PERFORMANCE[sym]["total"] += 1
                if outcome == "WIN":
                    SYMBOL_PERFORMANCE[sym]["wins"] += 1
                else:
                    SYMBOL_PERFORMANCE[sym]["losses"] += 1
            if setup and outcome in ("WIN", "LOSS"):
                SETUP_PERFORMANCE[setup]["total"] += 1
                if outcome == "WIN":
                    SETUP_PERFORMANCE[setup]["wins"] += 1
                else:
                    SETUP_PERFORMANCE[setup]["losses"] += 1
        logger.info("📊 Loaded perf: %d symbols, %d setups",
                    len(SYMBOL_PERFORMANCE), len(SETUP_PERFORMANCE))
    except Exception as e:
        logger.warning("Perf load failed: %s", e)


# ==============================================================================
# MT5
# ==============================================================================
def init_mt5() -> bool:
    if not mt5.initialize():
        logger.error("MT5 init failed: %s", mt5.last_error())
        return False
    logger.info("✅ MT5 connected — %s Scanner v6", cfg.BRAND)
    return True


# ==============================================================================
# SESSION
# ==============================================================================
def get_session() -> Tuple[str, float]:
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour < 16: return "OVERLAP", 1.12
    if 7  <= hour < 13: return "LONDON",  1.06
    if 16 <= hour < 20: return "NEWYORK", 1.04
    return "ASIAN", 0.92


def is_high_impact_news() -> bool:
    now = datetime.now(timezone.utc)
    for h, m in cfg.HIGH_IMPACT_NEWS:
        news_dt = datetime(now.year, now.month, now.day, h, m, tzinfo=timezone.utc)
        if abs((now - news_dt).total_seconds() / 60) <= cfg.NEWS_PAUSE_MINS:
            return True
    return False


# ==============================================================================
# SYMBOL LOOKUP
# ==============================================================================
def find_broker_symbol(base: str) -> Optional[str]:
    b = base.upper().replace(" ", "")
    candidates = [b]
    for suffix in (".A", ".M", ".RAW", ".ECN", ".PRO", ".CASH", "M", "C", "+"):
        if b.endswith(suffix):
            candidates.append(b[:-len(suffix)])
    if "BTC" in b:
        candidates.extend(["BTCUSD", "BTC", "BITCOIN", "BTCUSDT"])
    if "XAU" in b or "GOLD" in b:
        candidates.extend(["XAUUSD", "GOLD"])

    all_symbols = mt5.symbols_get() or []
    for candidate in candidates:
        for s in all_symbols:
            name = s.name.upper().replace(" ", "")
            if name == candidate:
                return s.name
    for s in all_symbols:
        name = s.name.upper().replace(" ", "")
        for candidate in candidates:
            if candidate and candidate in name and len(candidate) >= 3:
                return s.name
    return None


def get_spread_pips(broker_sym: str) -> float:
    tick = mt5.symbol_info_tick(broker_sym)
    info = mt5.symbol_info(broker_sym)
    if not tick or not info:
        return 999.0
    raw = tick.ask - tick.bid
    digits = info.digits
    pip_size = 10 ** -(digits - 1) if digits in (3, 5) else 10 ** -digits
    return round(raw / pip_size, 2) if pip_size else 999.0


def fetch_candles(symbol: str, timeframe: str, limit: int) -> List[dict]:
    try:
        tf_map = {
            "M5":  mt5.TIMEFRAME_M5,  "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30, "H1":  mt5.TIMEFRAME_H1,
            "H4":  mt5.TIMEFRAME_H4,  "D1":  mt5.TIMEFRAME_D1,
        }
        broker_sym = find_broker_symbol(symbol)
        if not broker_sym:
            return []
        mt5.symbol_select(broker_sym, True)
        time.sleep(0.05)
        rates = mt5.copy_rates_from_pos(
            broker_sym, tf_map.get(timeframe, mt5.TIMEFRAME_M15), 0, limit
        )
        if rates is None or len(rates) == 0:
            return []
        return [{
            "open":   float(r["open"]),  "high":  float(r["high"]),
            "low":    float(r["low"]),   "close": float(r["close"]),
            "volume": float(r["tick_volume"]), "time": int(r["time"]),
        } for r in rates]
    except Exception as e:
        logger.error("Fetch error %s %s: %s", symbol, timeframe, e)
        return []


# ==============================================================================
# INDICATORS
# ==============================================================================
def ema(values: List[float], period: int) -> float:
    if not values: return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def ema_series(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return [sum(values) / len(values)] * len(values) if values else []
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out = [seed] * period
    for v in values[period:]:
        seed = v * k + seed * (1 - k)
        out.append(seed)
    return out


def atr(candles: List[dict], period: int = 14) -> float:
    if len(candles) < period + 1: return 0.0
    trs = []
    for i in range(-period, 0):
        h, l = candles[i]["high"], candles[i]["low"]
        prev_c = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return sum(trs) / period


def rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1: return 50.0
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i - 1]
        if diff > 0: gains += diff
        else: losses += -diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def macd(closes: List[float]):
    if len(closes) < 35: return 0.0, 0.0, 0.0
    e12 = ema_series(closes, 12)
    e26 = ema_series(closes, 26)
    macd_series = [a - b for a, b in zip(e12, e26)]
    signal_line = ema(macd_series[-30:], 9) if len(macd_series) >= 9 else macd_series[-1]
    macd_line = macd_series[-1]
    return round(macd_line, 5), round(signal_line, 5), round(macd_line - signal_line, 5)


def bollinger(closes: List[float], period: int = 20, mult: float = 2.0):
    if len(closes) < period:
        m = closes[-1] if closes else 0
        return m, m, m, 0.5
    window = closes[-period:]
    middle = sum(window) / period
    var = sum((v - middle) ** 2 for v in window) / period
    std = var ** 0.5
    upper = middle + std * mult
    lower = middle - std * mult
    pct_b = (closes[-1] - lower) / (upper - lower) if upper != lower else 0.5
    return round(upper, 5), round(middle, 5), round(lower, 5), round(pct_b, 3)


def adx(candles: List[dict], period: int = 14) -> float:
    if len(candles) < period + 2: return 0.0
    p_dm, m_dm, tr = 0.0, 0.0, 0.0
    for i in range(-period, 0):
        hd = candles[i]["high"] - candles[i-1]["high"]
        ld = candles[i-1]["low"] - candles[i]["low"]
        p_dm += hd if (hd > ld and hd > 0) else 0
        m_dm += ld if (ld > hd and ld > 0) else 0
        h, l = candles[i]["high"], candles[i]["low"]
        prev_c = candles[i-1]["close"]
        tr += max(h - l, abs(h - prev_c), abs(l - prev_c))
    if tr == 0: return 0.0
    pdi = 100 * (p_dm / tr)
    mdi = 100 * (m_dm / tr)
    di_sum = pdi + mdi or 1
    return round(100 * abs(pdi - mdi) / di_sum, 2)


def stochastic(candles: List[dict], period: int = 14) -> float:
    if len(candles) < period: return 50.0
    window = candles[-period:]
    hi = max(c["high"] for c in window)
    lo = min(c["low"] for c in window)
    if hi == lo: return 50.0
    return round(100 * (candles[-1]["close"] - lo) / (hi - lo), 2)


def detect_rsi_divergence(closes: List[float], lookback: int = 20) -> str:
    if len(closes) < lookback + 14: return "NONE"
    rsi_series = []
    for i in range(lookback, 0, -1):
        rsi_series.append(rsi(closes[: -i + 1] if i > 1 else closes))
    if len(rsi_series) < lookback: return "NONE"
    recent = closes[-lookback:]
    low_idx = sorted(range(lookback), key=lambda i: recent[i])
    high_idx = sorted(range(lookback), key=lambda i: -recent[i])
    if len(low_idx) >= 2:
        a, b = sorted(low_idx[:2])
        if (recent[b] < recent[a] and rsi_series[b] > rsi_series[a] and b > a + 3):
            return "BULLISH_DIV"
    if len(high_idx) >= 2:
        a, b = sorted(high_idx[:2])
        if (recent[b] > recent[a] and rsi_series[b] < rsi_series[a] and b > a + 3):
            return "BEARISH_DIV"
    return "NONE"


def detect_swings(candles: List[dict], lookback: int = 5):
    highs, lows = [], []
    for i in range(lookback, len(candles) - lookback):
        if all(candles[i]["high"] >= candles[i + j]["high"]
               for j in range(-lookback, lookback + 1) if j != 0):
            highs.append(i)
        if all(candles[i]["low"] <= candles[i + j]["low"]
               for j in range(-lookback, lookback + 1) if j != 0):
            lows.append(i)
    return highs, lows


def market_structure(candles: List[dict]) -> str:
    if len(candles) < 30: return "RANGING"
    sh, sl = detect_swings(candles, 3)
    if len(sh) < 2 or len(sl) < 2: return "RANGING"
    last_hs = [candles[i]["high"] for i in sh[-2:]]
    last_ls = [candles[i]["low"]  for i in sl[-2:]]
    hh = last_hs[-1] > last_hs[-2]
    hl = last_ls[-1] > last_ls[-2]
    lh = last_hs[-1] < last_hs[-2]
    ll = last_ls[-1] < last_ls[-2]
    if hh and hl: return "BULLISH"
    if lh and ll: return "BEARISH"
    return "RANGING"


def key_support_resistance(candles: List[dict]):
    sh, sl = detect_swings(candles, 5)
    if not sh or not sl:
        rh = [c["high"] for c in candles[-20:]]
        rl = [c["low"]  for c in candles[-20:]]
        return min(rl), max(rh)
    key_res = max(candles[i]["high"] for i in sh[-3:]) if sh else candles[-1]["high"]
    key_sup = min(candles[i]["low"]  for i in sl[-3:]) if sl else candles[-1]["low"]
    return key_sup, key_res


def detect_regime(candles: List[dict]) -> str:
    if len(candles) < 30: return "RANGING"
    a_dx = adx(candles)
    closes = [c["close"] for c in candles]
    a = atr(candles)
    avg_close = sum(closes[-20:]) / 20
    atr_pct = (a / avg_close * 100) if avg_close else 0
    if a_dx > 25 and atr_pct > 0.25: return "TRENDING"
    if atr_pct > 1.3: return "VOLATILE"
    return "RANGING"


def usd_strength() -> str:
    try:
        dxy = find_broker_symbol("DXY") or find_broker_symbol("USDX")
        if not dxy: return "NEUTRAL"
        mt5.symbol_select(dxy, True)
        rates = mt5.copy_rates_from_pos(dxy, mt5.TIMEFRAME_H1, 0, 24)
        if rates is None or len(rates) < 12: return "NEUTRAL"
        closes = [r["close"] for r in rates]
        change = closes[-1] - closes[-12]
        if change > 0.15:  return "STRONG"
        if change < -0.15: return "WEAK"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def adaptive_conf(symbol: str, base: int, setup: str, session: str) -> int:
    conf = base
    sp = SYMBOL_PERFORMANCE[symbol]
    if sp["total"] >= 5:
        wr = sp["wins"] / sp["total"]
        if wr > 0.68: conf += 8
        elif wr < 0.40: conf -= 8
    st = SETUP_PERFORMANCE[setup]
    if st["total"] >= 5:
        wr = st["wins"] / st["total"]
        if wr > 0.70: conf += 10
        elif wr < 0.38: conf -= 7
    _, mult = get_session()
    conf = int(conf * mult)
    return min(95, max(35, conf))


def has_correlated_signal(symbol: str, db: Session) -> bool:
    for pair in cfg.CORRELATED_PAIRS.get(symbol, []):
        last = db.query(AISignal).filter_by(symbol=pair).order_by(
            AISignal.created_at.desc()).first()
        if last and last.created_at:
            age = (datetime.now(timezone.utc) - last.created_at).total_seconds()
            if age < cfg.CORR_COOLDOWN:
                return True
    return False


def compute_sl_tp(action: str, price: float, atr_val: float, regime: str,
                 key_sup: float, key_res: float,
                 sl_mult: float, tp_mult: float,
                 min_rr: float = 1.5) -> Tuple[float, float]:
    """
    Compute SL and TP anchored to current PRICE (not key levels) using ATR.
    Key levels are used as a sanity check / floor on SL — SL is allowed to
    be further from price than ATR*sl_mult if a structural level is just beyond,
    but never tighter than ATR*sl_mult (which would make trades stop out too fast).

    Critically: enforces R:R = tp_mult / sl_mult ratio in PRICE TERMS so R:R
    validation downstream actually passes.
    """
    if atr_val <= 0:
        # Fallback when ATR can't be computed
        atr_val = price * 0.0005  # 0.05% of price

    sl_dist = atr_val * sl_mult
    tp_dist = atr_val * tp_mult

    # Ensure tp_dist is at least min_rr × sl_dist
    if sl_dist > 0:
        required_tp_dist = sl_dist * min_rr
        if tp_dist < required_tp_dist:
            tp_dist = required_tp_dist

    if action == "BUY":
        sl = price - sl_dist
        tp = price + tp_dist
        # Use structural support as SL floor only if it's further from price (safer)
        if key_sup > 0 and key_sup < sl:
            sl = key_sup - atr_val * 0.2  # small buffer below support
            # Recompute tp to maintain min R:R
            sl_dist_new = price - sl
            if sl_dist_new > 0:
                tp = price + max(tp_dist, sl_dist_new * min_rr)
    else:  # SELL
        sl = price + sl_dist
        tp = price - tp_dist
        if key_res > 0 and key_res > sl:
            sl = key_res + atr_val * 0.2
            sl_dist_new = sl - price
            if sl_dist_new > 0:
                tp = price - max(tp_dist, sl_dist_new * min_rr)

    return sl, tp


def validate_rr(entry, sl, tp, action):
    if sl is None or tp is None: return False, 0.0
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk < 1e-8: return False, 0.0
    if action == "BUY"  and (sl >= entry or tp <= entry): return False, 0.0
    if action == "SELL" and (sl <= entry or tp >= entry): return False, 0.0
    rr = reward / risk
    return rr >= cfg.MIN_RR_RATIO, round(rr, 2)


def decide_action(buy_score: int, sell_score: int, confidence: int,
                  lean_threshold: int) -> str:
    if confidence < lean_threshold:
        return "WAIT"
    score_diff = abs(buy_score - sell_score)
    if score_diff < 6:
        return "WAIT"
    return "BUY" if buy_score > sell_score else "SELL"


# ==============================================================================
# STRATEGY: XAUUSD
# ==============================================================================
def analyze_xauusd(ltf, mtf, htf, symbol):
    closes_ltf = [c["close"] for c in ltf]
    highs_ltf  = [c["high"]  for c in ltf]
    lows_ltf   = [c["low"]   for c in ltf]
    a = atr(ltf)
    r = rsi(closes_ltf)
    a_dx = adx(ltf)
    stoch_k = stochastic(ltf)
    macd_l, macd_s, macd_h = macd(closes_ltf)
    bb_u, bb_m, bb_l, bb_pct = bollinger(closes_ltf, 20)
    ema9   = ema(closes_ltf, 9)
    ema21  = ema(closes_ltf, 21)
    ema50  = ema(closes_ltf, 50)
    ema200 = ema(closes_ltf, 200) if len(closes_ltf) >= 200 else ema(closes_ltf, 50)
    divergence = detect_rsi_divergence(closes_ltf, 20)
    structure = market_structure(ltf)
    key_sup, key_res = key_support_resistance(ltf)

    mtf_closes = [c["close"] for c in mtf] if mtf else closes_ltf
    mtf_ema21 = ema(mtf_closes, 21); mtf_ema50 = ema(mtf_closes, 50)
    mtf_trend = "BULLISH" if mtf_ema21 > mtf_ema50 else "BEARISH"

    htf_closes = [c["close"] for c in htf] if htf else closes_ltf
    htf_ema20 = ema(htf_closes, 20); htf_ema50 = ema(htf_closes, 50)
    htf_trend = "BULLISH" if htf_ema20 > htf_ema50 * 1.0002 else \
                "BEARISH" if htf_ema20 < htf_ema50 * 0.9998 else "NEUTRAL"

    regime = detect_regime(ltf)
    session, _ = get_session()
    usd = usd_strength()
    news = is_high_impact_news()
    price = closes_ltf[-1]
    recent_high = max(highs_ltf[-15:]); recent_low = min(lows_ltf[-15:])
    buy, sell, setups = 0, 0, []

    if htf_trend == "BULLISH" and mtf_trend == "BULLISH":
        buy += 20; setups.append("HTF_MTF_BULL")
        if abs(price - ema21) / ema21 < 0.003 and price > ema50:
            buy += 15; setups.append("PULLBACK_BUY")
        if ema9 > ema21 > ema50 > ema200:
            buy += 8; setups.append("EMA_STACK_BULL")
    elif htf_trend == "BEARISH" and mtf_trend == "BEARISH":
        sell += 20; setups.append("HTF_MTF_BEAR")
        if abs(price - ema21) / ema21 < 0.003 and price < ema50:
            sell += 15; setups.append("PULLBACK_SELL")
        if ema9 < ema21 < ema50 < ema200:
            sell += 8; setups.append("EMA_STACK_BEAR")
    elif htf_trend == "BULLISH": buy += 10
    elif htf_trend == "BEARISH": sell += 10

    last = ltf[-1]
    if last["high"] > recent_high and last["close"] < recent_high * 0.9998:
        sell += 22; setups.append("LIQ_SWEEP_HIGH")
    if last["low"] < recent_low and last["close"] > recent_low * 1.0002:
        buy += 22; setups.append("LIQ_SWEEP_LOW")

    hour = datetime.now(timezone.utc).hour
    if (7 <= hour <= 9 or 13 <= hour <= 15) and a_dx > 20:
        if macd_h > 0 and price > ema21:
            buy += 14; setups.append("SESSION_MOMENTUM_BULL")
        elif macd_h < 0 and price < ema21:
            sell += 14; setups.append("SESSION_MOMENTUM_BEAR")

    bb_width = (bb_u - bb_l) / bb_m if bb_m else 0
    if bb_width > 0.004:
        if bb_pct > 0.80 and macd_h > 0 and a_dx > 22:
            buy += 12; setups.append("BB_EXPANSION_BULL")
        elif bb_pct < 0.20 and macd_h < 0 and a_dx > 22:
            sell += 12; setups.append("BB_EXPANSION_BEAR")

    if divergence == "BULLISH_DIV": buy += 18; setups.append("BULL_DIV")
    elif divergence == "BEARISH_DIV": sell += 18; setups.append("BEAR_DIV")

    if macd_l > macd_s and macd_h > 0: buy += 6
    if macd_l < macd_s and macd_h < 0: sell += 6
    if structure == "BULLISH": buy += 6
    if structure == "BEARISH": sell += 6
    if usd == "WEAK":   buy += 8
    if usd == "STRONG": sell += 8
    if stoch_k < 20: buy += 4
    if stoch_k > 80: sell += 4

    if r > 78: buy -= 12
    if r < 22: sell -= 12

    if buy < 5 and sell < 5:
        if ema9 > ema21: buy += 10; setups.append("BIAS_BULL")
        elif ema9 < ema21: sell += 10; setups.append("BIAS_BEAR")

    base = 38 + max(buy, sell)
    base = min(94, base + 5)
    primary = setups[0] if setups else "GENERAL"
    confidence = adaptive_conf(symbol, base, primary, session)

    trend_label = "BULLISH" if buy > sell + 3 else "BEARISH" if sell > buy + 3 else "NEUTRAL"
    lean, _, _ = get_thresholds(symbol)
    action = decide_action(buy, sell, confidence, lean)

    sl_mult = 0.7 if regime == "RANGING" else 1.0 if regime == "TRENDING" else 1.3
    tp_mult = 2.5 if regime == "RANGING" else 3.0 if regime == "TRENDING" else 2.2
    sl, tp = compute_sl_tp(action, price, a, regime, key_sup, key_res, sl_mult, tp_mult)

    reasons = [f"USD:{usd}", f"Session:{session}", f"Regime:{regime}"]
    if setups: reasons.append(setups[0].replace("_", " ").title())
    if news: reasons.append("⚠️ HIGH IMPACT NEWS")

    return {
        "signal": action, "confidence": confidence, "trend": trend_label,
        "structure": structure, "reason": " | ".join(reasons[:5]),
        "current_price": price, "stop_loss": sl, "take_profit": tp,
        "rr_ratio": tp_mult / sl_mult, "usd_strength": usd, "session": session,
        "regime": regime, "setup_type": primary, "rsi": r, "adx": a_dx,
        "macd_hist": macd_h, "bb_pct": bb_pct, "news_active": news,
        "setups_active": setups, "buy_score": buy, "sell_score": sell,
    }


# ==============================================================================
# STRATEGY: BTCUSD
# ==============================================================================
def analyze_btcusd(ltf, mtf, htf, symbol):
    closes_ltf = [c["close"] for c in ltf]
    highs_ltf  = [c["high"]  for c in ltf]
    lows_ltf   = [c["low"]   for c in ltf]
    a = atr(ltf); r = rsi(closes_ltf); a_dx = adx(ltf)
    macd_l, macd_s, macd_h = macd(closes_ltf)
    bb_u, bb_m, bb_l, bb_pct = bollinger(closes_ltf, 20)
    ema9 = ema(closes_ltf, 9); ema21 = ema(closes_ltf, 21); ema50 = ema(closes_ltf, 50)
    ema200 = ema(closes_ltf, 200) if len(closes_ltf) >= 200 else ema(closes_ltf, max(len(closes_ltf)-1, 50))
    divergence = detect_rsi_divergence(closes_ltf, 20) if len(closes_ltf) >= 34 else "NONE"
    structure = market_structure(ltf)
    key_sup, key_res = key_support_resistance(ltf)

    htf_closes = [c["close"] for c in htf] if htf else closes_ltf
    htf_ema21 = ema(htf_closes, 21); htf_ema50 = ema(htf_closes, 50)
    htf_trend = "BULLISH" if htf_ema21 > htf_ema50 else "BEARISH"

    mtf_closes = [c["close"] for c in mtf] if mtf else closes_ltf
    mtf_ema21 = ema(mtf_closes, 21); mtf_ema50 = ema(mtf_closes, 50)
    mtf_trend = "BULLISH" if mtf_ema21 > mtf_ema50 else "BEARISH"

    regime = detect_regime(ltf)
    session, _ = get_session()
    news = is_high_impact_news()
    price = closes_ltf[-1]
    recent_high = max(highs_ltf[-15:]); recent_low = min(lows_ltf[-15:])
    buy, sell, setups = 0, 0, []

    vol_avg = sum(c["volume"] for c in ltf[-15:-1]) / 14 if len(ltf) >= 15 else 1
    vol_now = ltf[-1]["volume"]
    vol_ratio = (vol_now / vol_avg) if vol_avg else 1
    if vol_ratio > 1.5:
        if price > recent_high * 0.9998 and macd_h > 0:
            buy += 25; setups.append("VOL_BREAKOUT_BULL")
        elif price < recent_low * 1.0002 and macd_h < 0:
            sell += 25; setups.append("VOL_BREAKOUT_BEAR")

    if htf_trend == "BULLISH" and abs(price - ema21) / ema21 < 0.008 and price > ema50:
        buy += 20; setups.append("EMA_PULLBACK_BULL")
    elif htf_trend == "BEARISH" and abs(price - ema21) / ema21 < 0.008 and price < ema50:
        sell += 20; setups.append("EMA_PULLBACK_BEAR")

    if htf_trend == "BULLISH" and mtf_trend == "BULLISH":
        buy += 16; setups.append("HTF_MTF_BULL")
    elif htf_trend == "BEARISH" and mtf_trend == "BEARISH":
        sell += 16; setups.append("HTF_MTF_BEAR")

    if ema9 > ema21 > ema50 and macd_h > 0:
        buy += 16; setups.append("MOMENTUM_STACK_BULL")
    elif ema9 < ema21 < ema50 and macd_h < 0:
        sell += 16; setups.append("MOMENTUM_STACK_BEAR")

    if divergence == "BULLISH_DIV": buy += 16; setups.append("BULL_DIV")
    elif divergence == "BEARISH_DIV": sell += 16; setups.append("BEAR_DIV")

    nearest_thousand = round(price / 1000) * 1000
    if abs(price - nearest_thousand) / price < 0.003 and a_dx > 20:
        if price > nearest_thousand and macd_h > 0:
            buy += 10; setups.append("ROUND_NUMBER_BREAK_UP")
        elif price < nearest_thousand and macd_h < 0:
            sell += 10; setups.append("ROUND_NUMBER_REJECT_DOWN")

    if a_dx > 28:
        if price > ema21: buy += 6
        else: sell += 6
    if structure == "BULLISH": buy += 6
    if structure == "BEARISH": sell += 6

    if r > 80: buy -= 14
    if r < 20: sell -= 14

    if buy < 5 and sell < 5:
        if ema9 > ema21: buy += 12; setups.append("BIAS_BULL")
        elif ema9 < ema21: sell += 12; setups.append("BIAS_BEAR")

    base = 38 + max(buy, sell)
    base = min(94, base + 5)
    primary = setups[0] if setups else "GENERAL"
    confidence = adaptive_conf(symbol, base, primary, session)

    trend_label = "BULLISH" if buy > sell + 3 else "BEARISH" if sell > buy + 3 else "NEUTRAL"
    lean, _, _ = get_thresholds(symbol)
    action = decide_action(buy, sell, confidence, lean)

    sl_mult = 1.0 if regime == "RANGING" else 1.2 if regime == "TRENDING" else 1.5
    tp_mult = 3.0 if regime == "RANGING" else 3.5 if regime == "TRENDING" else 2.5
    sl, tp = compute_sl_tp(action, price, a, regime, key_sup, key_res, sl_mult, tp_mult)

    reasons = [f"Session:{session}", f"Regime:{regime}"]
    if vol_ratio > 1.5: reasons.append(f"Vol×{vol_ratio:.1f}")
    if setups: reasons.append(setups[0].replace("_", " ").title())
    if news: reasons.append("⚠️ HIGH IMPACT NEWS")

    return {
        "signal": action, "confidence": confidence, "trend": trend_label,
        "structure": structure, "reason": " | ".join(reasons[:5]),
        "current_price": price, "stop_loss": sl, "take_profit": tp,
        "rr_ratio": tp_mult / sl_mult, "session": session, "regime": regime,
        "setup_type": primary, "rsi": r, "adx": a_dx, "macd_hist": macd_h,
        "bb_pct": bb_pct, "news_active": news, "setups_active": setups,
        "buy_score": buy, "sell_score": sell,
    }


# ==============================================================================
# STRATEGY: FOREX
# ==============================================================================
def analyze_forex(ltf, mtf, htf, symbol):
    closes_ltf = [c["close"] for c in ltf]
    highs_ltf  = [c["high"]  for c in ltf]
    lows_ltf   = [c["low"]   for c in ltf]
    a = atr(ltf); r = rsi(closes_ltf); a_dx = adx(ltf)
    macd_l, macd_s, macd_h = macd(closes_ltf)
    bb_u, bb_m, bb_l, bb_pct = bollinger(closes_ltf, 20)
    ema9 = ema(closes_ltf, 9); ema21 = ema(closes_ltf, 21); ema50 = ema(closes_ltf, 50)
    divergence = detect_rsi_divergence(closes_ltf, 20)
    structure = market_structure(ltf)
    key_sup, key_res = key_support_resistance(ltf)

    mtf_closes = [c["close"] for c in mtf] if mtf else closes_ltf
    mtf_ema21 = ema(mtf_closes, 21); mtf_ema50 = ema(mtf_closes, 50)
    mtf_trend = "BULLISH" if mtf_ema21 > mtf_ema50 else "BEARISH"

    htf_closes = [c["close"] for c in htf] if htf else closes_ltf
    htf_ema20 = ema(htf_closes, 20); htf_ema50 = ema(htf_closes, 50)
    htf_trend = "BULLISH" if htf_ema20 > htf_ema50 else "BEARISH"

    regime = detect_regime(ltf)
    session, _ = get_session()
    usd = usd_strength()
    news = is_high_impact_news()
    price = closes_ltf[-1]
    recent_high = max(highs_ltf[-15:]); recent_low = min(lows_ltf[-15:])
    buy, sell, setups = 0, 0, []

    if htf_trend == "BULLISH" and mtf_trend == "BULLISH":
        buy += 24; setups.append("HTF_MTF_ALIGNED_BUY")
        if price > ema21 and macd_h > 0: buy += 12
    elif htf_trend == "BEARISH" and mtf_trend == "BEARISH":
        sell += 24; setups.append("HTF_MTF_ALIGNED_SELL")
        if price < ema21 and macd_h < 0: sell += 12

    last = ltf[-1]
    if last["high"] > recent_high and last["close"] < recent_high * 0.9998:
        sell += 16; setups.append("SUPPLY_REJECTION")
    if last["low"] < recent_low and last["close"] > recent_low * 1.0002:
        buy += 16; setups.append("DEMAND_REACTION")

    if htf_trend == "BULLISH" and abs(price - ema21) / ema21 < 0.0015:
        buy += 12; setups.append("EMA21_PULLBACK_BULL")
    elif htf_trend == "BEARISH" and abs(price - ema21) / ema21 < 0.0015:
        sell += 12; setups.append("EMA21_PULLBACK_BEAR")

    is_usd_quote = symbol.upper().endswith("USD")
    is_usd_base  = symbol.upper().startswith("USD")
    if is_usd_quote:
        if usd == "WEAK":   buy += 10
        if usd == "STRONG": sell += 10
    elif is_usd_base:
        if usd == "STRONG": buy += 10
        if usd == "WEAK":   sell += 10

    if divergence == "BULLISH_DIV": buy += 12; setups.append("BULL_DIV")
    elif divergence == "BEARISH_DIV": sell += 12; setups.append("BEAR_DIV")

    if macd_l > macd_s and macd_h > 0: buy += 6
    if macd_l < macd_s and macd_h < 0: sell += 6
    if a_dx > 22:
        if price > ema21: buy += 6
        else: sell += 6
    if structure == "BULLISH": buy += 6
    if structure == "BEARISH": sell += 6
    if r > 75: buy -= 10
    if r < 25: sell -= 10

    if buy < 5 and sell < 5:
        if ema9 > ema21: buy += 8; setups.append("BIAS_BULL")
        elif ema9 < ema21: sell += 8; setups.append("BIAS_BEAR")

    base = 38 + max(buy, sell)
    primary = setups[0] if setups else "GENERAL"
    confidence = adaptive_conf(symbol, base, primary, session)

    trend_label = "BULLISH" if buy > sell + 3 else "BEARISH" if sell > buy + 3 else "NEUTRAL"
    lean, _, _ = get_thresholds(symbol)
    action = decide_action(buy, sell, confidence, lean)

    sl_mult = 0.7 if regime == "RANGING" else 0.9 if regime == "TRENDING" else 1.2
    tp_mult = 2.0 if regime == "RANGING" else 2.5 if regime == "TRENDING" else 1.8
    sl, tp = compute_sl_tp(action, price, a, regime, key_sup, key_res, sl_mult, tp_mult)

    reasons = [f"USD:{usd}", f"Session:{session}", f"Regime:{regime}"]
    if setups: reasons.append(setups[0].replace("_", " ").title())
    if news: reasons.append("⚠️ HIGH IMPACT NEWS")

    return {
        "signal": action, "confidence": confidence, "trend": trend_label,
        "structure": structure, "reason": " | ".join(reasons[:5]),
        "current_price": price, "stop_loss": sl, "take_profit": tp,
        "rr_ratio": tp_mult / sl_mult, "usd_strength": usd, "session": session,
        "regime": regime, "setup_type": primary, "rsi": r, "adx": a_dx,
        "macd_hist": macd_h, "bb_pct": bb_pct, "news_active": news,
        "setups_active": setups, "buy_score": buy, "sell_score": sell,
    }


def analyze_symbol(symbol: str) -> dict:
    WAIT = {"signal": "WAIT", "confidence": 0, "trend": "NEUTRAL", "reason": ""}
    try:
        ltf = fetch_candles(symbol, "M15", cfg.LTF_CANDLES)
        mtf = fetch_candles(symbol, "H1",  cfg.MTF_CANDLES)
        htf = fetch_candles(symbol, "H4",  cfg.HTF_CANDLES)
        if len(ltf) < cfg.MIN_CANDLES_REQUIRED:
            return {**WAIT, "reason": f"Insufficient data ({len(ltf)} candles)"}
        sym = symbol.upper()
        if "XAU" in sym or "GOLD" in sym:
            return analyze_xauusd(ltf, mtf, htf, symbol)
        if "BTC" in sym or "BITCOIN" in sym:
            return analyze_btcusd(ltf, mtf, htf, symbol)
        return analyze_forex(ltf, mtf, htf, symbol)
    except Exception as e:
        logger.error("Analysis error %s: %s", symbol, e, exc_info=True)
        return {**WAIT, "reason": str(e)}


def save_market_state_and_signal(db: Session, symbol: str, result: dict,
                                wide_spread: bool = False) -> Optional[str]:
    """
    Saves dashboard state always.
    Saves AISignal row only if all gates pass.
    Returns gate name that blocked (for diagnostic logging) or None if saved.
    """
    # Cast all NumPy float64 to native Python floats
    def _f(x):
        if x is None: return None
        try: return float(x)
        except (TypeError, ValueError): return None

    state = db.query(AIMarketState).filter_by(symbol=symbol).first()
    if not state:
        state = AIMarketState(symbol=symbol)
        db.add(state)
    state.signal      = result["signal"]
    state.trend       = result["trend"]
    state.confidence  = int(result["confidence"]) if result.get("confidence") else 0
    state.entry       = _f(result.get("current_price", 0))
    state.stop_loss   = _f(result.get("stop_loss"))
    state.take_profit = _f(result.get("take_profit"))
    state.analysis    = result["reason"][:500]
    db.commit()

    if result["signal"] == "WAIT":
        return "wait"
    _, _, signal_threshold = get_thresholds(symbol)
    if result["confidence"] < signal_threshold:
        return f"low_conf_{int(result['confidence'])}_<{signal_threshold}"
    if result.get("news_active"):
        return "news_pause"
    if wide_spread:
        return "wide_spread"

    rr_ok, rr = validate_rr(
        result["current_price"], result.get("stop_loss"),
        result.get("take_profit"), result["signal"],
    )
    if not rr_ok:
        return "bad_rr"
    if has_correlated_signal(symbol, db):
        return "correlated_cooldown"

    last_sig = db.query(AISignal).filter_by(symbol=symbol).order_by(
        AISignal.created_at.desc()).first()
    if last_sig and last_sig.created_at:
        age = (datetime.now(timezone.utc) - last_sig.created_at).total_seconds()
        if age < cfg.SIGNAL_COOLDOWN and last_sig.action == result["signal"]:
            return f"signal_cooldown_{int(cfg.SIGNAL_COOLDOWN - age)}s_left"

    new_signal = AISignal(
        symbol = symbol, timeframe = "M15",
        action = result["signal"], confidence = int(result["confidence"]),
        entry_price = _f(result.get("current_price", 0)),
        stop_loss = _f(result.get("stop_loss")),
        take_profit = _f(result.get("take_profit")),
        trend = result["trend"], structure = result.get("structure"),
        risk_reward_ratio = _f(rr), entry_quality = result.get("setup_type"),
        liquidity_sweep = (
            "HIGH" if any("SWEEP_HIGH" in s for s in result.get("setups_active", [])) else
            "LOW"  if any("SWEEP_LOW"  in s for s in result.get("setups_active", [])) else
            None
        ),
        created_at = datetime.now(timezone.utc),
    )
    db.add(new_signal)
    db.commit()

    priority_tag = "⭐" if is_priority(symbol) else " "
    logger.info("🚀 %s SIGNAL SAVED → %s %s | conf=%d%% | RR=%.2f | %s",
                priority_tag, result["signal"], symbol,
                result["confidence"], rr, result.get("setup_type", "N/A"))
    return None


def scan_all_symbols(db: Session) -> None:
    symbols = [r.symbol for r in db.query(AISymbol).filter(AISymbol.enabled == True).all()]
    bull, bear, wait = 0, 0, 0
    strongest_sym, strongest_conf = None, 0
    saved_count = 0
    blocked_reasons = defaultdict(int)
    saved_signals_summary = []  # for logging

    for symbol in symbols:
        if _shutdown: break
        try:
            broker_sym = find_broker_symbol(symbol)
            wide_spread = False
            spread_value = 0
            spread_limit = get_spread_limit(symbol)
            if broker_sym:
                spread_value = get_spread_pips(broker_sym)
                if spread_value > spread_limit:
                    wide_spread = True
            result = analyze_symbol(symbol)
            if wide_spread and result["signal"] != "WAIT":
                result["reason"] += f" | Spread {spread_value:.1f}p > {spread_limit:.0f}p limit"

            gate = save_market_state_and_signal(db, symbol, result, wide_spread=wide_spread)
            if gate is None:
                saved_count += 1
                saved_signals_summary.append(f"{symbol}={result['signal']}")
            elif gate not in ("wait",):
                # Tag wide_spread with the specific symbol so user can see what's blocked
                if gate == "wide_spread":
                    blocked_reasons[f"wide_spread_{symbol}_{spread_value:.0f}p"] += 1
                else:
                    blocked_reasons[gate] += 1

            if result["signal"] == "BUY":  bull += 1
            elif result["signal"] == "SELL": bear += 1
            else: wait += 1
            if result["confidence"] > strongest_conf:
                strongest_conf = result["confidence"]
                strongest_sym = symbol
        except Exception as e:
            logger.error("Scan error %s: %s", symbol, e, exc_info=True)
            db.rollback()

    logger.info("📊 Cycle: %d BUY | %d SELL | %d WAIT | strongest=%s (%d%%)",
                bull, bear, wait, strongest_sym, strongest_conf)

    # New diagnostic: did we actually save any AISignal rows this cycle?
    if saved_count > 0:
        logger.info("💾 SAVED %d signal(s) this cycle: %s",
                    saved_count, ", ".join(saved_signals_summary))
    if blocked_reasons:
        reasons_str = ", ".join(f"{k}={v}" for k, v in blocked_reasons.items())
        logger.info("🚧 Blocked (had direction but didn't save): %s", reasons_str)


def main():
    if not init_mt5():
        logger.critical("❌ MT5 connection failed")
        return

    with SessionLocal() as db:
        load_performance_from_db(db)

    logger.info(
        "🚀 %s Scanner v6 | priority(XAU/BTC) lean=%d display=%d signal=%d | standard lean=%d display=%d signal=%d",
        cfg.BRAND, cfg.PRIORITY_LEAN, cfg.PRIORITY_DISPLAY, cfg.PRIORITY_SIGNAL,
        cfg.STANDARD_LEAN, cfg.STANDARD_DISPLAY, cfg.STANDARD_SIGNAL,
    )

    while not _shutdown:
        start = time.time()
        db = SessionLocal()
        try:
            scan_all_symbols(db)
        except Exception as e:
            logger.error("Main error: %s", e, exc_info=True)
        finally:
            db.close()
        elapsed = time.time() - start
        sleep_time = max(10, cfg.SCAN_INTERVAL - int(elapsed))
        logger.info("⏱ Cycle %.1fs | next %ds", elapsed, sleep_time)
        time.sleep(sleep_time)

    logger.info("✅ %s shut down cleanly", cfg.BRAND)
    mt5.shutdown()


if __name__ == "__main__":
    main()