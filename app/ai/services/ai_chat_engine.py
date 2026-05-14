import MetaTrader5 as mt5
import statistics


def calculate_rsi(closes, period=14):

    gains = []
    losses = []

    for i in range(1, len(closes)):

        change = closes[i] - closes[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)

        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    rsi = 100 - (100 / (1 + rs))

    return round(rsi, 2)


def analyze_market(symbol: str):

    if not mt5.initialize():

        return {
            "signal": "WAIT",
            "trend": "UNKNOWN",
            "confidence": 0,
            "current_price": 0,
            "stop_loss": 0,
            "take_profit": 0,
            "assistant_response":
                "MT5 connection failed."
        }

    rates = mt5.copy_rates_from_pos(
        symbol,
        mt5.TIMEFRAME_M5,
        0,
        50
    )

    higher_rates = mt5.copy_rates_from_pos(
        symbol,
        mt5.TIMEFRAME_M15,
        0,
        100
    )

    if rates is None:

        return {
            "signal": "WAIT",
            "trend": "UNKNOWN",
            "confidence": 0,
            "current_price": 0,
            "stop_loss": 0,
            "take_profit": 0,
            "assistant_response":
                "No market data available."
        }

    closes = [candle["close"] for candle in rates]

    highs = [
        candle["high"]
        for candle in rates
    ]

    lows = [
        candle["low"]
        for candle in rates
    ]

    higher_closes = [
        candle["close"]
        for candle in higher_rates
    ]

    highs = [candle["high"] for candle in rates]

    lows = [candle["low"] for candle in rates]

    ranges = [
        highs[i] - lows[i]
        for i in range(len(highs))
    ]

    average_range = statistics.mean(ranges)

    support = min(lows[-20:])
    resistance = max(highs[-20:])

    last_high = highs[-1]
    last_low = lows[-1]

    swing_high_1 = highs[-5]

    swing_high_2 = highs[-10]

    swing_low_1 = lows[-5]

    swing_low_2 = lows[-10]

    current_price = closes[-1]

    average_price = statistics.mean(closes)
    
    rsi = calculate_rsi(closes)

    higher_high = (
        swing_high_1 > swing_high_2
    )

    lower_low = (
        swing_low_1 < swing_low_2
    )

    bullish_bos = (
        current_price > swing_high_2
    )

    bearish_bos = (
        current_price < swing_low_2
    )

    bullish_choch = (
        lower_low
        and bullish_bos
    )

    bearish_choch = (
        higher_high
        and bearish_bos
    )

    previous_high = highs[-2]
    previous_low = lows[-2]

    bullish_liquidity_sweep = (
        last_low < support
        and current_price > support
    )

    bearish_liquidity_sweep = (
        last_high > resistance
        and current_price < resistance
    )

    distance_to_support = (
        current_price - support
    )

    distance_to_resistance = (
        resistance - current_price
    )

    higher_average = statistics.mean(
        higher_closes
    )

    higher_current = higher_closes[-1]

    higher_bullish = (
        higher_current > higher_average
    )

    higher_bearish = (
        higher_current < higher_average
    )

    bullish_candles = 0
    bearish_candles = 0

    trend_continuation_bullish = False
    trend_continuation_bearish = False

    trend_strength = abs(
        bullish_candles - bearish_candles
    )

    for candle in rates[-10:]:

        if candle["close"] > candle["open"]:
            bullish_candles += 1
        else:
            bearish_candles += 1

        if (
            bullish_candles >= 7
            and current_price > average_price
            and higher_bullish
        ):
            trend_continuation_bullish = True

        if (
            bearish_candles >= 7
            and current_price < average_price
            and higher_bearish
        ):
            trend_continuation_bearish = True   

    # =========================
    # BUY CONDITIONS
    # =========================

    if (
        current_price > average_price
        and bullish_candles >= 6
        and rsi > 55
        and rsi < 75
        and average_range > 2
        and higher_bullish
        and distance_to_resistance > 3
    ):
        
        signal = "BUY"

        trend = "BULLISH"

        confidence = 90 + bullish_candles

        if trend_continuation_bullish:
            confidence += 12

        if bullish_liquidity_sweep:
            confidence += 8

        if bullish_bos:
            confidence += 5

        if bullish_choch:
            confidence += 10   

        stop_loss = min(lows[-10:])

        take_profit = resistance

    # =========================
    # SELL CONDITIONS
    # =========================

    elif (
        current_price < average_price
        and bearish_candles >= 6
        and rsi < 45
        and rsi > 25
        and average_range > 2
        and higher_bearish
        and distance_to_support > 3
    ):
        
        signal = "SELL"

        trend = "BEARISH"

        confidence = 90 + bearish_candles

        if trend_continuation_bearish:
            confidence += 12

        if bearish_liquidity_sweep:
            confidence += 8

        if bearish_bos:
            confidence += 5

        if bearish_choch:
            confidence += 10  

        stop_loss = max(highs[-10:])

        take_profit = support

    # =========================
    # NO TRADE
    # =========================

    else:

        signal = "WAIT"

        trend = "LOW VOLATILITY"

        confidence = 50

        stop_loss = 0

        take_profit = 0

    liquidity_message = ""

    structure_message = ""

    if bullish_bos:
        structure_message += (
            "Bullish BOS detected. "
        )

    if bearish_bos:
        structure_message += (
            "Bearish BOS detected. "
        )

    if bullish_choch:
        structure_message += (
            "Bullish CHOCH detected. "
        )

    if bearish_choch:
        structure_message += (
            "Bearish CHOCH detected. "
        )

    if bullish_liquidity_sweep:
           liquidity_message = (
               "Bullish liquidity sweep detected. "
        )

    elif bearish_liquidity_sweep:
        liquidity_message = (
            "Bearish liquidity sweep detected. "
        ) 

    return {

        "signal": signal,

        "trend": trend,

        "confidence": int(confidence),

        "current_price": float(round(current_price, 2)),

        "stop_loss": float(round(stop_loss, 2)),

        "take_profit": float(round(take_profit, 2)),

        "rsi": float(rsi),

        "volatility": float(round(average_range, 2)),

        "higher_timeframe_trend":
            "BULLISH"
            if higher_bullish
            else "BEARISH",

        "support": float(round(support, 2)),

        "resistance": float(round(resistance, 2)),

        "bullish_liquidity_sweep":
            bullish_liquidity_sweep,

        "bearish_liquidity_sweep":
            bearish_liquidity_sweep,

        "bullish_bos":
            bullish_bos,

        "bearish_bos":
            bearish_bos,

        "bullish_choch":
            bullish_choch,

        "bearish_choch":
            bearish_choch,   

        "assistant_response":
            f"{liquidity_message}"
            f"{structure_message}"
            f"{symbol} trend is {trend}. "
            f"Signal is {signal}. "
            f"Confidence is {confidence}%."
    }