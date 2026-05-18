import pandas as pd


class AIScanner:

    def analyze_market(self, candles):

        # =====================================================
        # VALIDATION
        # =====================================================

        if not candles or len(candles) < 50:

            return {
                "signal": "WAIT",
                "confidence": 0,
                "trend": "UNKNOWN",
                "reason": "Not enough candles"
            }

        # =====================================================
        # EXTRACT DATA
        # =====================================================

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        opens = [c["open"] for c in candles]

        current_price = closes[-1]

        # =====================================================
        # PANDAS SERIES
        # =====================================================

        close_series = pd.Series(closes)

        # =====================================================
        # EMA TREND
        # =====================================================

        ema_fast = close_series.ewm(span=20).mean()
        ema_slow = close_series.ewm(span=50).mean()

        # =====================================================
        # RSI
        # =====================================================

        delta = close_series.diff()

        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()

        rs = avg_gain / avg_loss

        rsi = 100 - (100 / (1 + rs))

        latest_rsi = rsi.iloc[-1]

        # =====================================================
        # TREND DETECTION
        # =====================================================

        trend = "RANGING"

        if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
            trend = "BULLISH"

        elif ema_fast.iloc[-1] < ema_slow.iloc[-1]:
            trend = "BEARISH"

        # =====================================================
        # MARKET STRUCTURE
        # =====================================================

        recent_highs = highs[-15:]
        recent_lows = lows[-15:]

        resistance = max(recent_highs)
        support = min(recent_lows)

        volatility = resistance - support

        structure = "RANGING"

        if current_price > resistance - (volatility * 0.1):
            structure = "BREAKOUT_UP"

        elif current_price < support + (volatility * 0.1):
            structure = "BREAKOUT_DOWN"

        # =====================================================
        # LIQUIDITY SWEEP
        # =====================================================

        liquidity_sweep = "NONE"

        last_high = highs[-1]
        last_low = lows[-1]

        if (
            last_high > resistance
            and current_price < resistance
        ):

            liquidity_sweep = "SWEEP_HIGH"

        elif (
            last_low < support
            and current_price > support
        ):

            liquidity_sweep = "SWEEP_LOW"

        # =====================================================
        # MOMENTUM
        # =====================================================

        bullish_count = 0
        bearish_count = 0

        recent_closes = closes[-10:]

        for i in range(1, len(recent_closes)):

            if recent_closes[i] > recent_closes[i - 1]:
                bullish_count += 1

            elif recent_closes[i] < recent_closes[i - 1]:
                bearish_count += 1

        # =====================================================
        # CANDLE STRENGTH
        # =====================================================

        last_open = opens[-1]
        last_close = closes[-1]

        candle_body = abs(last_close - last_open)

        entry_quality = "WEAK"

        if candle_body > 0.5:
            entry_quality = "STRONG"

        elif candle_body > 0.2:
            entry_quality = "GOOD"

        # =====================================================
        # SIGNAL ENGINE
        # =====================================================

        signal = "WAIT"
        confidence = 50
        reason = "No setup"

        # BUY CONDITIONS

        if (

            trend == "BULLISH"

            and latest_rsi > 50

            and bullish_count >= bearish_count

            and structure != "BREAKOUT_DOWN"

        ):

            signal = "BUY"
            confidence = 75
            reason = "Bullish trend confirmation"

        # SELL CONDITIONS

        elif (

            trend == "BEARISH"

            and latest_rsi < 50

            and bearish_count >= bullish_count

            and structure != "BREAKOUT_UP"

        ):

            signal = "SELL"
            confidence = 75
            reason = "Bearish trend confirmation"

        # =====================================================
        # CONFIDENCE BOOSTS
        # =====================================================

        if volatility > 3:
            confidence += 10

        elif volatility > 1:
            confidence += 5

        if entry_quality == "STRONG":
            confidence += 5

        elif entry_quality == "GOOD":
            confidence += 2

        if liquidity_sweep != "NONE":
            confidence += 5

        # RSI POWER

        if signal == "BUY" and latest_rsi > 65:
            confidence += 5

        elif signal == "SELL" and latest_rsi < 35:
            confidence += 5

        # LIMITS

        if confidence > 95:
            confidence = 95

        # =====================================================
        # SL / TP ENGINE
        # =====================================================

        stop_loss = None
        take_profit = None

        risk_reward_ratio = 2

        if signal == "BUY":

            stop_loss = support - (volatility * 0.2)

            risk = current_price - stop_loss

            take_profit = (
                current_price
                + (risk * risk_reward_ratio)
            )

        elif signal == "SELL":

            stop_loss = resistance + (volatility * 0.2)

            risk = stop_loss - current_price

            take_profit = (
                current_price
                - (risk * risk_reward_ratio)
            )

        # =====================================================
        # FINAL RESPONSE
        # =====================================================

        return {

            "signal": signal,

            "trend": trend,

            "confidence": confidence,

            "reason": reason,

            "entry_quality": entry_quality,

            "structure": structure,

            "liquidity_sweep": liquidity_sweep,

            "rsi": round(latest_rsi, 2),

            "current_price": round(current_price, 2),

            "support": round(support, 2),

            "resistance": round(resistance, 2),

            "volatility": round(volatility, 2),

            "stop_loss":
                round(stop_loss, 2)
                if stop_loss else None,

            "take_profit":
                round(take_profit, 2)
                if take_profit else None,

            "risk_reward_ratio":
                risk_reward_ratio,

            "bullish_candles":
                bullish_count,

            "bearish_candles":
                bearish_count
        }