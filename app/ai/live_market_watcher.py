import time
import logging
from datetime import datetime
import MetaTrader5 as mt5
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ai.models.ai_market_state import AIMarketState
from app.models import AISignal, AISymbol
from app.ai.services.ai_scanner import AIScanner   # Updated scanner above

# =========================
# CONFIGURATION
# =========================

SCANNER = AIScanner()
MIN_CONFIDENCE = 58
MIN_RR_RATIO = 1.6

def get_ai_symbols():

    db = SessionLocal()

    try:

        rows = (
            db.query(AISymbol)
            .filter(AISymbol.enabled == True)
            .all()
        )

        return [r.symbol for r in rows]

    finally:
        db.close()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# =========================
# MT5 INIT
# =========================
def init_mt5():
    if not mt5.initialize():
        logging.error(f"MT5 init failed: {mt5.last_error()}")
        return False
    logging.info("✅ MT5 Connected")
    return True

MT5_CONNECTED = init_mt5()

# =========================
# FIND BROKER SYMBOL
# =========================
def find_broker_symbol(base_symbol):

    symbols = mt5.symbols_get()

    if not symbols:
        return None

    # Exact match first
    for s in symbols:

        if s.name == base_symbol:
            return s.name

    # Flexible match
    for s in symbols:

        name = s.name.upper()

        if base_symbol.upper() in name:
            return s.name

    return None

# =========================
# MULTI-TIMEFRAME FETCH
# =========================
def fetch_candles(symbol: str, timeframe: str, limit: int = 500):
    if not mt5.terminal_info():

        logging.warning("MT5 disconnected. Reconnecting...")

        mt5.shutdown()

        time.sleep(2)

        if not init_mt5():
            return []

    try:
        tf_map = {
            "M5": mt5.TIMEFRAME_M5,
            "H1": mt5.TIMEFRAME_H1,
        }
        mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_M5)

        broker_symbol = find_broker_symbol(symbol)

        if not broker_symbol:

            logging.warning(f"{symbol} not found on broker")

            return []

        symbol_info = mt5.symbol_info(broker_symbol)

        logging.info(f"{symbol} → Broker Symbol: {broker_symbol}")

        mt5.symbol_select(broker_symbol, True)
        rates = mt5.copy_rates_from_pos(
            broker_symbol,
            mt5_tf,
            0,
            limit
        )

        if rates is None or len(rates) == 0:
            return []

        return [{
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r["tick_volume"]),
            "time": int(r["time"])
        } for r in rates]

    except Exception as e:
        logging.error(f"Fetch error {symbol} {timeframe}: {e}")
        return []


def analyze_symbol(symbol: str):
    try:
        ltf_candles = fetch_candles(symbol, "M5", 600)
        htf_candles = fetch_candles(symbol, "H1", 300)

        if len(ltf_candles) < 80:
            return {"signal": "WAIT", "confidence": 0, "reason": "Not enough data"}

        analysis = SCANNER.analyze_market(ltf_candles, htf_candles)

        # Flexible filter
        if analysis.get("rr_ratio", 0) < MIN_RR_RATIO and analysis["signal"] != "WAIT":
            analysis["confidence"] = max(50, analysis["confidence"] - 12)

        if analysis.get("atr", 0) <= 0:
            logging.info(f"Low volatility for {symbol}")
            return {
                "signal": "WAIT",
                "confidence": 0,
                "reason": "Low volatility"
            }   

        return analysis

    except Exception as e:
        logging.error(f"Analysis error {symbol}: {e}")
        return {"signal": "WAIT", "confidence": 0, "reason": str(e)}


# =========================
# SAVE + SIGNAL LOGIC
# =========================
def save_market_state():
    db: Session = SessionLocal()
    try:
        symbols = get_ai_symbols()

        logging.info(f"ACTIVE AI SYMBOLS: {symbols}")

        for symbol in symbols:
            logging.info(f"🔍 Multi-TF Scan: {symbol}")

            analysis = analyze_symbol(symbol)

            # Update Market State
            existing = db.query(AIMarketState).filter_by(symbol=symbol).first()
            if not existing:
                existing = AIMarketState(symbol=symbol)
                db.add(existing)

            existing.signal = analysis.get("signal", "WAIT")
            existing.trend = analysis.get("trend", "UNKNOWN")
            existing.confidence = int(analysis.get("confidence", 0))
            existing.entry = float(analysis.get("current_price", 0))
            existing.stop_loss = float(analysis.get("stop_loss")) if analysis.get("stop_loss") else None
            existing.take_profit = float(analysis.get("take_profit")) if analysis.get("take_profit") else None
            existing.analysis = analysis.get("reason", "")[:500]

            db.commit()

            # New Signal
            last_signal = (
                db.query(AISignal)
                .filter_by(symbol=symbol)
                .order_by(AISignal.created_at.desc())
                .first()
            )

            recent_duplicate = False

            if last_signal and last_signal.created_at:

                signal_age = (
                    datetime.datetime.utcnow() - last_signal.created_at
                ).total_seconds()

                if (
                    last_signal.action == analysis["signal"]
                    and signal_age < 900
                ):
                    recent_duplicate = True

                if (
                    last_signal.action == analysis["signal"]
                    and signal_age < 900
                ):
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
                    entry_price=float(analysis["current_price"]),
                    stop_loss=float(analysis["stop_loss"]) if analysis.get("stop_loss") else None,
                    take_profit=float(analysis["take_profit"]) if analysis.get("take_profit") else None,
                    trend=analysis["trend"]
                )
                db.add(new_signal)
                db.commit()

                logging.info(
                    f"🚀 NEW SIGNAL → {analysis['signal']} {symbol} | "
                    f"Conf: {analysis['confidence']}% | HTF: {analysis['trend']} | "
                    f"{analysis.get('reason', '')[:120]}..."
                )
            else:
                logging.info(f"⏳ No signal {symbol} (Conf: {analysis.get('confidence')}%)")

    except Exception as e:
        logging.error(f"Watcher error: {e}")
    finally:
        db.close()


# =========================
# MAIN LOOP
# =========================
if __name__ == "__main__":
    if not MT5_CONNECTED:
        logging.error("❌ MT5 Connection Failed")
    else:
        logging.info("🚀 Professional Multi-Timeframe AI Watcher Started (MT5)")
        logging.info(
            f"Dynamic AI Symbols Loaded | HTF: H1 + LTF: M5"
        )

        while True:
            start = time.time()
            save_market_state()
            elapsed = time.time() - start
            sleep_time = max(40, 60 - int(elapsed))
            logging.info(f"Cycle completed in {elapsed:.1f}s | Sleeping {sleep_time}s...\n")
            time.sleep(sleep_time)