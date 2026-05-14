class AIScanner:

    def analyze_market(self, candles):

        if len(candles) < 20:
            return {
                "signal": "WAIT",
                "confidence": 0,
                "trend": "UNKNOWN"
            }

        closes = [
            candle["close"]
            for candle in candles
        ]

        highs = [
            candle["high"]
            for candle in candles
        ]

        lows = [
            candle["low"]
            for candle in candles
        ]

        recent_closes = closes[-10:]

        average_price = (
            sum(recent_closes)
            / len(recent_closes)
        )

        current_price = recent_closes[-1]

        last_candle = candles[-1]

        last_open = last_candle["open"]
        last_close = last_candle["close"]

        candle_body = abs(
            last_close - last_open
        )

        entry_quality = "WEAK"

        if candle_body > 3:
            entry_quality = "STRONG"

        elif candle_body > 1.5:
            entry_quality = "GOOD"

        highest_price = max(highs[-10:])

        lowest_price = min(lows[-10:])

        volatility = highest_price - lowest_price

        trend = "RANGING"
        signal = "WAIT"
        confidence = 50

        bullish_count = 0
        bearish_count = 0

        for i in range(1, len(recent_closes)):

            if recent_closes[i] > recent_closes[i - 1]:
                bullish_count += 1

            elif recent_closes[i] < recent_closes[i - 1]:
                bearish_count += 1

        # Bullish Trend
        if (
            current_price > average_price
            and bullish_count > bearish_count
        ):

            trend = "BULLISH"
            signal = "BUY"
            confidence = 75

        # Bearish Trend
        elif (
            current_price < average_price
            and bearish_count > bullish_count
        ):

            trend = "BEARISH"
            signal = "SELL"
            confidence = 75

        # Volatility Boost
        if volatility > 8:
            confidence += 10

        elif volatility > 4:
            confidence += 5

        # Momentum Boost
        momentum = abs(
            current_price - average_price
        )

        confidence += int(momentum)

        if entry_quality == "STRONG":
            confidence += 5

        elif entry_quality == "GOOD":
            confidence += 2

        if confidence > 95:
            confidence = 95

        # =========================
        # MARKET STRUCTURE LOGIC
        # =========================

        recent_highs = highs[-5:]
        recent_lows = lows[-5:]

        resistance = max(recent_highs)
        support = min(recent_lows)

        structure = "RANGING"
        liquidity_sweep = "NONE"

        last_high = highs[-1]
        last_low = lows[-1]

        # Bullish breakout
        if current_price > resistance - 1:
            structure = "BREAKOUT_UP"

        # Bearish breakout
        elif current_price < support + 1:
            structure = "BREAKOUT_DOWN"

        # Liquidity sweep above highs
        if (
            last_high > resistance
            and current_price < resistance
        ):
            liquidity_sweep = "SWEEP_HIGH"

        # Liquidity sweep below lows
        elif (
              last_low < support
              and current_price > support
        ):
              liquidity_sweep = "SWEEP_LOW"

        stop_loss = None
        take_profit = None
        risk_reward_ratio = 2

        if signal == "BUY":

            stop_loss = support - 2

            risk = current_price - stop_loss

            take_profit = (
                current_price
                + (risk * risk_reward_ratio)
            )

        elif signal == "SELL":

            stop_loss = resistance + 2

            risk = stop_loss - current_price

            take_profit = (
                current_price
                - (risk * risk_reward_ratio)
            )

        return {
            "signal": signal,
            "trend": trend,
            "structure": structure,
            "liquidity_sweep": liquidity_sweep,
            "entry_quality": entry_quality,
            "stop_loss": round(stop_loss, 2) if stop_loss else None,
            "take_profit": round(take_profit, 2) if take_profit else None,
            "risk_reward_ratio": risk_reward_ratio,
            "confidence": confidence,
            "current_price": round(current_price, 2),
            "average_price": round(average_price, 2),
            "volatility": round(volatility, 2),
            "support": round(support, 2),
            "resistance": round(resistance, 2),
            "bullish_candles": bullish_count,
            "bearish_candles": bearish_count
        }