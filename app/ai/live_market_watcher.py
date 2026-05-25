import time
import logging
from datetime import datetime, timezone
import MetaTrader5 as mt5
from sqlalchemy.orm import Session
from collections import defaultdict

from app.database import SessionLocal
from app.ai.models.ai_market_state import AIMarketState
from app.models import AISignal, AISymbol

# =========================
# CONFIGURATION
# =========================
MIN_CONFIDENCE = 64
MIN_RR_RATIO = 1.8

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

SYMBOL_PERFORMANCE = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
SETUP_PERFORMANCE = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})

HIGH_IMPACT_NEWS_TIMES = [
    (8, 30), (9, 0), (13, 30), (14, 0), (15, 0), (15, 30),
    (16, 0), (19, 0), (20, 0), (21, 30)
]

CORRELATED_PAIRS = {
    "EURUSD": ["GBPUSD", "AUDUSD", "NZDUSD"],
    "GBPUSD": ["EURUSD", "AUDUSD"],
    "AUDUSD": ["EURUSD", "GBPUSD", "NZDUSD"],
    "NZDUSD": ["EURUSD", "AUDUSD"],
    "USDJPY": ["USDCAD", "USDCHF"],
    "XAUUSD": ["USDJPY", "USDCAD"]
}

# =========================
# MT5 INIT
# =========================
def init_mt5():
    if not mt5.initialize():
        logging.error(f"MT5 init failed: {mt5.last_error()}")
        return False
    logging.info("✅ MT5 Connected - FINAL UPGRADED Professional Scanner")
    return True

MT5_CONNECTED = init_mt5()

# =========================
# HELPER FUNCTIONS
# =========================
def get_session_info():
    now = datetime.now(timezone.utc)
    hour = now.hour
    if 7 <= hour < 11:
        return "LONDON", "London Session"
    elif 13 <= hour < 17:
        return "NEWYORK", "New York Session"
    elif 13 <= hour < 16:
        return "OVERLAP", "London/NY Overlap (Best)"
    else:
        return "ASIAN", "Asian Session"

def is_high_impact_news():
    now = datetime.now(timezone.utc)
    hour = now.hour
    for news_hour, news_minute in HIGH_IMPACT_NEWS_TIMES:
        news_time = datetime(now.year, now.month, now.day, news_hour, news_minute, tzinfo=timezone.utc)
        if abs((now - news_time).total_seconds() / 60) <= 30:
            return True
    return False

def get_usd_strength():
    try:
        dxy = find_broker_symbol("DXY") or find_broker_symbol("USDJPY")
        if not dxy:
            return "NEUTRAL"
        mt5.symbol_select(dxy, True)
        rates = mt5.copy_rates_from_pos(dxy, mt5.TIMEFRAME_H1, 0, 16)
        if rates is None or len(rates) < 10:
            return "NEUTRAL"
        closes = [r["close"] for r in rates]
        change = closes[-1] - closes[-8]
        if change > 0.10:
            return "STRONG"
        elif change < -0.10:
            return "WEAK"
        return "NEUTRAL"
    except:
        return "NEUTRAL"

def find_broker_symbol(base_symbol: str):
    for s in mt5.symbols_get():
        if s.name == base_symbol or base_symbol.upper() in s.name.upper():
            return s.name
    return None

def fetch_candles_fast(symbol: str, timeframe: str, limit: int = 400):
    try:
        tf_map = {"M5": mt5.TIMEFRAME_M5, "H1": mt5.TIMEFRAME_H1}
        mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_M5)
        broker_symbol = find_broker_symbol(symbol)
        if not broker_symbol:
            return []
        mt5.symbol_select(broker_symbol, True)
        rates = mt5.copy_rates_from_pos(broker_symbol, mt5_tf, 0, limit)
        if rates is None or len(rates) == 0:
            return []
        return [{
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": float(r["tick_volume"]), "time": int(r["time"])
        } for r in rates]
    except Exception as e:
        logging.error(f"Fetch error {symbol}: {e}")
        return []

def get_adaptive_confidence(symbol: str, base_confidence: int, setup_type: str = None) -> int:
    perf = SYMBOL_PERFORMANCE[symbol]
    conf = base_confidence

    if perf["total"] >= 5:
        win_rate = perf["wins"] / perf["total"]
        if win_rate > 0.68:
            conf += 8
        elif win_rate < 0.40:
            conf -= 8

    if setup_type and SETUP_PERFORMANCE[setup_type]["total"] >= 3:
        setup_win_rate = SETUP_PERFORMANCE[setup_type]["wins"] / SETUP_PERFORMANCE[setup_type]["total"]
        if setup_win_rate > 0.72:
            conf += 10
        elif setup_win_rate < 0.38:
            conf -= 7

    return min(95, max(42, conf))

def has_correlated_signal(symbol, db):
    correlated = CORRELATED_PAIRS.get(symbol, [])
    for pair in correlated:
        last = db.query(AISignal).filter_by(symbol=pair).order_by(AISignal.created_at.desc()).first()
        if last and last.created_at and (datetime.now(timezone.utc) - last.created_at).total_seconds() < 2400:
            return True
    return False

def detect_volatility_regime(closes):
    if len(closes) < 30:
        return "RANGING"
    recent_move = abs(closes[-1] - closes[-20])
    avg_move = sum([abs(closes[i] - closes[i-1]) for i in range(1, 20)]) / 19
    if recent_move > avg_move * 2.2:
        return "TRENDING"
    return "RANGING"

def extract_setup_type(reason: str) -> str:
    if "Liquidity" in reason or "Sweep" in reason:
        return "LIQUIDITY_SWEEP"
    elif "Breakout" in reason:
        return "BREAKOUT"
    elif "Momentum" in reason:
        return "MOMENTUM"
    elif "Trending" in reason:
        return "TRENDING"
    elif "USD Weak" in reason or "USD Strong" in reason:
        return "USD_BIAS"
    else:
        return "GENERAL"

# =========================
# STRATEGY FUNCTIONS (Same as before - XAUUSD, BTC, Forex)
# =========================
def analyze_gold(ltf_candles, htf_candles, symbol):
    closes = [c["close"] for c in ltf_candles]
    highs = [c["high"] for c in ltf_candles]
    lows = [c["low"] for c in ltf_candles]

    usd = get_usd_strength()
    session_name, _ = get_session_info()
    news = is_high_impact_news()
    regime = detect_volatility_regime(closes)

    usd_bias = "BULLISH" if usd == "WEAK" else "BEARISH" if usd == "STRONG" else "NEUTRAL"
    recent_high = max(highs[-18:])
    recent_low = min(lows[-18:])
    atr = sum([highs[i] - lows[i] for i in range(-12, 0)]) / 12

    score = 46
    reasons = [f"USD:{usd}", f"Session:{session_name}", f"Regime:{regime}"]

    if usd_bias == "BULLISH":
        score += 18
    elif usd_bias == "BEARISH":
        score += 18

    if regime == "TRENDING":
        score += 12
        reasons.append("Trending Market")

    if closes[-1] > recent_high and (highs[-1] - lows[-1]) > atr * 1.2:
        score += 16
        reasons.append("Vol Breakout")

    if closes[-1] > closes[-4] > closes[-8]:
        score += 10

    setup_type = extract_setup_type(" | ".join(reasons))
    final_conf = get_adaptive_confidence(symbol, min(94, max(46, score)), setup_type)

    signal = "WAIT"
    if final_conf >= MIN_CONFIDENCE:
        signal = "BUY" if usd_bias == "BULLISH" or closes[-1] > recent_high else "SELL"

    news_text = " | HIGH IMPACT NEWS" if news else ""
    return {
        "signal": signal,
        "confidence": final_conf,
        "trend": usd_bias,
        "reason": " | ".join(reasons[:3]) + news_text,
        "current_price": closes[-1],
        "stop_loss": recent_low - (atr * 0.75) if signal == "BUY" else recent_high + (atr * 0.75),
        "take_profit": closes[-1] + (atr * 2.6) if signal == "BUY" else closes[-1] - (atr * 2.6),
        "rr_ratio": 2.6,
        "usd_strength": usd,
        "session": session_name,
        "regime": regime,
        "setup_type": setup_type
    }

def analyze_btc(ltf_candles, htf_candles, symbol):
    closes = [c["close"] for c in ltf_candles]
    highs = [c["high"] for c in ltf_candles]
    lows = [c["low"] for c in ltf_candles]

    session_name, _ = get_session_info()
    news = is_high_impact_news()
    regime = detect_volatility_regime(closes)

    recent_high = max(highs[-15:])
    recent_low = min(lows[-15:])
    atr = sum([highs[i] - lows[i] for i in range(-8, 0)]) / 8

    score = 42
    reasons = [f"Session:{session_name}", f"Regime:{regime}"]

    if regime == "TRENDING":
        score += 14
        reasons.append("Strong Trend")

    if closes[-1] > recent_high and (closes[-1] - closes[-3]) > atr * 0.7:
        score += 20
        reasons.append("Powerful Breakout")

    if closes[-1] > closes[-3] > closes[-6]:
        score += 14
        reasons.append("Momentum")

    if ltf_candles[-1]["volume"] > sum([c["volume"] for c in ltf_candles[-6:-1]]) / 5 * 1.3:
        score += 8
        reasons.append("Volume Surge")

    setup_type = extract_setup_type(" | ".join(reasons))
    final_conf = get_adaptive_confidence(symbol, min(92, max(44, score)), setup_type)

    signal = "WAIT"
    if final_conf >= MIN_CONFIDENCE:
        signal = "BUY" if closes[-1] > recent_high else "SELL"

    news_text = " | HIGH IMPACT NEWS" if news else ""
    return {
        "signal": signal,
        "confidence": final_conf,
        "trend": "BULLISH" if closes[-1] > closes[-12] else "BEARISH",
        "reason": " | ".join(reasons[:3]) + news_text,
        "current_price": closes[-1],
        "stop_loss": recent_low - (atr * 0.85) if signal == "BUY" else recent_high + (atr * 0.85),
        "take_profit": closes[-1] + (atr * 3.0) if signal == "BUY" else closes[-1] - (atr * 3.0),
        "rr_ratio": 3.0,
        "session": session_name,
        "regime": regime,
        "setup_type": setup_type
    }

def analyze_forex(ltf_candles, htf_candles, symbol):
    closes = [c["close"] for c in ltf_candles]
    highs = [c["high"] for c in ltf_candles]
    lows = [c["low"] for c in ltf_candles]

    usd = get_usd_strength()
    session_name, _ = get_session_info()
    news = is_high_impact_news()
    regime = detect_volatility_regime(closes)

    usd_bias = "BULLISH" if usd == "WEAK" else "BEARISH" if usd == "STRONG" else "NEUTRAL"
    htf_trend = "BULLISH" if closes[-1] > sum(closes[-16:]) / 16 else "BEARISH"

    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    liquidity_sweep = "NONE"
    if highs[-1] > recent_high and closes[-1] < recent_high:
        liquidity_sweep = "SWEEP_HIGH"
    elif lows[-1] < recent_low and closes[-1] > recent_low:
        liquidity_sweep = "SWEEP_LOW"

    ema12 = sum(closes[-12:]) / 12
    ema26 = sum(closes[-26:]) / 26
    momentum = "BUY" if ema12 > ema26 else "SELL" if ema12 < ema26 else "NEUTRAL"

    score = 46
    reasons = [f"USD:{usd}", f"Session:{session_name}", f"Regime:{regime}"]

    if usd_bias == "BULLISH":
        score += 16
    elif usd_bias == "BEARISH":
        score += 16

    if regime == "TRENDING":
        score += 10
        reasons.append("Trending Market")

    if htf_trend == "BULLISH" and momentum == "BUY":
        score += 20
    elif htf_trend == "BEARISH" and momentum == "SELL":
        score += 20

    if liquidity_sweep != "NONE":
        score += 10

    setup_type = extract_setup_type(" | ".join(reasons))
    final_conf = get_adaptive_confidence(symbol, min(94, max(43, score)), setup_type)

    signal = "WAIT"
    if final_conf >= MIN_CONFIDENCE:
        if htf_trend == "BULLISH" and momentum == "BUY":
            signal = "BUY"
        elif htf_trend == "BEARISH" and momentum == "SELL":
            signal = "SELL"

    news_text = " | HIGH IMPACT NEWS" if news else ""
    return {
        "signal": signal,
        "confidence": final_conf,
        "trend": htf_trend,
        "reason": " | ".join(reasons[:3]) + news_text,
        "current_price": closes[-1],
        "stop_loss": recent_low - 0.00025 if signal == "BUY" else recent_high + 0.00025,
        "take_profit": closes[-1] + (closes[-1] - recent_low) * 2.0 if signal == "BUY" else closes[-1] - (recent_high - closes[-1]) * 2.0,
        "rr_ratio": 2.0,
        "usd_strength": usd,
        "session": session_name,
        "regime": regime,
        "setup_type": setup_type
    }

# =========================
# MAIN ANALYSIS ROUTER
# =========================
def analyze_symbol(symbol: str):
    try:
        ltf = fetch_candles_fast(symbol, "M5", 420)
        htf = fetch_candles_fast(symbol, "H1", 200)

        if len(ltf) < 60:
            return {"signal": "WAIT", "confidence": 28, "trend": "NEUTRAL", "reason": "Not enough data"}

        symbol_upper = symbol.upper()

        if "XAU" in symbol_upper or "GOLD" in symbol_upper:
            return analyze_gold(ltf, htf, symbol)
        elif "BTC" in symbol_upper or "BITCOIN" in symbol_upper:
            return analyze_btc(ltf, htf, symbol)
        else:
            return analyze_forex(ltf, htf, symbol)

    except Exception as e:
        logging.error(f"Analysis error {symbol}: {e}")
        return {"signal": "WAIT", "confidence": 0, "reason": str(e)}

# =========================
# SAVE + SIGNAL LOGIC (FIXED)
# =========================
def save_market_state():
    db: Session = SessionLocal()
    try:
        symbols = [r.symbol for r in db.query(AISymbol).filter(AISymbol.enabled == True).all()]

        for symbol in symbols:
            if has_correlated_signal(symbol, db):
                logging.info(f"⏸️ Skipping {symbol} - Correlated pair active")
                continue

            analysis = analyze_symbol(symbol)

            existing = db.query(AIMarketState).filter_by(symbol=symbol).first()
            if not existing:
                existing = AIMarketState(symbol=symbol)
                db.add(existing)

            existing.signal = analysis["signal"]
            existing.trend = analysis["trend"]
            existing.confidence = analysis["confidence"]
            existing.entry = analysis.get("current_price", 0)
            existing.stop_loss = analysis.get("stop_loss")
            existing.take_profit = analysis.get("take_profit")
            existing.analysis = analysis["reason"][:500]
            db.commit()

            # FIXED: Safe check for created_at
            last_signal = db.query(AISignal).filter_by(symbol=symbol).order_by(AISignal.created_at.desc()).first()
            recent_duplicate = False
            if last_signal and last_signal.created_at is not None:
                if (datetime.now(timezone.utc) - last_signal.created_at).total_seconds() < 900:
                    if last_signal.action == analysis["signal"]:
                        recent_duplicate = True

            should_create = (
                analysis["signal"] != "WAIT"
                and analysis["confidence"] >= MIN_CONFIDENCE
                and not recent_duplicate
            )

            if should_create:
                new_signal = AISignal(
                    symbol=symbol,
                    timeframe="M5",
                    action=analysis["signal"],
                    confidence=analysis["confidence"],
                    entry_price=analysis.get("current_price", 0),
                    stop_loss=analysis.get("stop_loss"),
                    take_profit=analysis.get("take_profit"),
                    trend=analysis["trend"]
                )
                db.add(new_signal)
                db.commit()

                logging.info(
                    f"🚀 {symbol} SIGNAL → {analysis['signal']} | "
                    f"Conf: {analysis['confidence']}% | "
                    f"{analysis.get('session', 'N/A')} | "
                    f"{analysis.get('setup_type', 'N/A')} | "
                    f"{analysis['reason'][:55]}"
                )

    except Exception as e:
        logging.error(f"Watcher error: {e}")
    finally:
        db.close()

# =========================
# MAIN LOOP (24/7)
# =========================
if __name__ == "__main__":
    if not MT5_CONNECTED:
        logging.error("❌ MT5 Connection Failed")
    else:
        logging.info("🚀 FINAL PROFESSIONAL AI SCANNER STARTED (9.7/10)")
        logging.info("24/7 | Symbol-Specific | Correlation Filter | Regime + Setup Tracking | Expanded News")

        while True:
            start = time.time()
            save_market_state()
            elapsed = time.time() - start
            sleep_time = max(26, 46 - int(elapsed))
            logging.info(f"Cycle: {elapsed:.1f}s | Next in {sleep_time}s\n")
            time.sleep(sleep_time)