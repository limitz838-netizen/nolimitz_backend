from fastapi import APIRouter, Query

from app.ai.services.ai_market_reader import AIMarketReader
from app.ai.services.ai_scanner import AIScanner
from app.models import AISignal
from app.database import SessionLocal


router = APIRouter(
    prefix="/api/ai",
    tags=["AI Market"]
)


TIMEFRAME_MAP = {
    "M1": "M1",
    "M5": "M5",
    "M15": "M15",
    "M30": "M30",
    "H1": "H1",
    "H4": "H4"
}


@router.get("/candles")
def get_candles(
    symbol: str = Query("XAUUSD"),
    timeframe: str = Query("M5")
):

    reader = AIMarketReader()

    tf = TIMEFRAME_MAP.get(
        timeframe.upper(),
        "M5"
    )

    candles = reader.get_candles(
        symbol=symbol,
        timeframe=tf
    )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "count": len(candles),
        "candles": candles
    }


@router.get("/scan")
def scan_market(
    symbol: str = Query("XAUUSD"),
    timeframe: str = Query("M5")
):

    reader = AIMarketReader()

    tf = TIMEFRAME_MAP.get(
        timeframe.upper(),
        "M5"
    )

    candles = reader.get_candles(
        symbol=symbol,
        timeframe=tf
    )

    scanner = AIScanner()

    analysis = scanner.analyze_market(
        candles
    )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "analysis": analysis
    }


@router.get("/multi-timeframe-scan")
def multi_timeframe_scan(
    symbol: str = Query("XAUUSD")
):

    reader = AIMarketReader()

    scanner = AIScanner()

    timeframes = {
        "M5": "M5",
        "M15": "M15",
        "H1": "H1"
    }

    results = {}

    bullish_count = 0
    bearish_count = 0

    # =========================
    # ANALYZE TIMEFRAMES
    # =========================

    for tf_name, tf_value in timeframes.items():

        candles = reader.get_candles(
            symbol=symbol,
            timeframe=tf_value
        )

        analysis = scanner.analyze_market(
            candles
        )

        results[tf_name] = analysis

        if analysis["trend"] == "BULLISH":
            bullish_count += 1

        elif analysis["trend"] == "BEARISH":
            bearish_count += 1

    # =========================
    # FINAL SIGNAL
    # =========================

    final_signal = "WAIT"
    confidence = 50

    if bullish_count >= 2:
        final_signal = "BUY"
        confidence = 90

    elif bearish_count >= 2:
        final_signal = "SELL"
        confidence = 90

    # =========================
    # SAVE SIGNAL
    # =========================

    if final_signal != "WAIT":

        db = SessionLocal()

        main_tf = results["H1"]

        trend = main_tf["trend"]

        current_price = main_tf["current_price"]

        stop_loss = main_tf.get("stop_loss")

        take_profit = main_tf.get("take_profit")

        # =========================
        # FALLBACK STOP LOSS
        # =========================

        if stop_loss is None:

            if final_signal == "BUY":
                stop_loss = current_price - 20

            elif final_signal == "SELL":
                stop_loss = current_price + 20

        # =========================
        # FALLBACK TAKE PROFIT
        # =========================

        if take_profit is None:

            if final_signal == "BUY":
                take_profit = current_price + 40

            elif final_signal == "SELL":
                take_profit = current_price - 40

        new_signal = AISignal(
            symbol=symbol,
            timeframe="MULTI",
            signal=final_signal,
            confidence=confidence,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trend=trend,
        )

        db.add(new_signal)

        db.commit()

        db.close()

    # =========================
    # RETURN RESPONSE
    # =========================

    return {
        "symbol": symbol,
        "multi_timeframe_analysis": results,
        "final_signal": final_signal,
        "confidence": confidence
    }


@router.get("/signals")
def get_ai_signals():

    db = SessionLocal()

    signals = (
        db.query(AISignal)
        .order_by(AISignal.id.desc())
        .all()
    )

    db.close()

    return signals