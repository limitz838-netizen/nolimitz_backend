import pandas as pd
import numpy as np

class AIScanner:

    def analyze_market(self, candles: list[dict]) -> dict:
        """
        Professional SMC + Price Action Scanner
        Designed to generate real trading signals with strong reasoning.
        """
        if not candles or len(candles) < 250:
            return {"signal": "WAIT", "confidence": 0, "trend": "UNKNOWN", "reason": "Not enough data"}

        # ====================== DATA PREP ======================
        df = pd.DataFrame(candles)
        df = df[['open', 'high', 'low', 'close']].astype(float)
        
        closes = df['close']
        highs = df['high']
        lows = df['low']
        opens = df['open']
        current_price = closes.iloc[-1]

        # ====================== MULTI-TIMEFRAME SIMULATION ======================
        # Higher TF bias (roughly 4-5x slower)
        len_htf = max(20, len(df) // 4)
        htf_closes = closes.iloc[-len_htf:]
        htf_highs = highs.iloc[-len_htf:]
        htf_lows = lows.iloc[-len_htf:]

        # ====================== CORE INDICATORS ======================
        ema21 = closes.ewm(span=21).mean()
        ema50 = closes.ewm(span=50).mean()
        ema200 = closes.ewm(span=200).mean()

        # RSI
        delta = closes.diff()
        rsi = 100 - (100 / (1 + (delta.where(delta > 0, 0).rolling(14).mean() / 
                                 -delta.where(delta < 0, 0).rolling(14).mean())))
        latest_rsi = rsi.iloc[-1]

        # MACD
        macd_line = closes.ewm(span=12).mean() - closes.ewm(span=26).mean()
        signal_line = macd_line.ewm(span=9).mean()
        macd_hist = macd_line - signal_line

        # ATR
        tr = pd.concat([
            highs - lows,
            abs(highs - closes.shift()),
            abs(lows - closes.shift())
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        latest_atr = atr.iloc[-1] or 0.0001

        # ADX (Trend Strength)
        plus_dm = highs.diff()
        minus_dm = lows.diff() * -1

        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0

        tr_smooth = tr.rolling(14).mean()

        plus_di = 100 * (
            plus_dm.rolling(14).mean() / tr_smooth
        )

        minus_di = 100 * (
            minus_dm.rolling(14).mean() / tr_smooth
        )

        dx = (
            abs(plus_di - minus_di)
            / (plus_di + minus_di)
        ) * 100

        adx = dx.rolling(14).mean()

        latest_adx = float(adx.iloc[-1])

        if np.isnan(latest_adx):
            latest_adx = 20

        # ====================== SMC / PRICE ACTION ======================
        # Recent structure
        recent_high = htf_highs.max()
        recent_low = htf_lows.min()
        volatility = recent_high - recent_low

        # Liquidity Sweeps
        liquidity_sweep = "NONE"
        if highs.iloc[-1] > recent_high and closes.iloc[-1] < recent_high - (volatility * 0.05):
            liquidity_sweep = "SWEEP_HIGH"
        elif lows.iloc[-1] < recent_low and closes.iloc[-1] > recent_low + (volatility * 0.05):
            liquidity_sweep = "SWEEP_LOW"

        # Fair Value Gap (simple 3-candle imbalance)
        fvg = "NONE"

        if len(df) >= 3:

           # Bullish FVG
           if lows.iloc[-1] > highs.iloc[-3]:
               fvg = "BULLISH"

           # Bearish FVG
           elif highs.iloc[-1] < lows.iloc[-3]:
               fvg = "BEARISH"

        # Break of Structure (BOS)
        bos = "NONE"
        if closes.iloc[-1] > htf_highs[:-1].max():
            bos = "BULLISH_BOS"
        elif closes.iloc[-1] < htf_lows[:-1].min():
            bos = "BEARISH_BOS"

        # Strong candle
        body = abs(closes.iloc[-1] - opens.iloc[-1])
        is_strong_candle = body > 0.5 * latest_atr

        # ====================== HIGHER TIMEFRAME BIAS ======================
        htf_bias = "BULLISH" if htf_closes.iloc[-1] > ema200.iloc[-len_htf:].mean() else "BEARISH"

        # ====================== CONFLUENCE SCORING (Permissive) ======================
        score = 40  # Base score - allows more signals
        reasons = []

        # Trend Alignment (Heavy weight)
        if ema21.iloc[-1] > ema50.iloc[-1] and closes.iloc[-1] > ema200.iloc[-1] and htf_bias == "BULLISH":
            score += 30
            reasons.append("Strong bullish alignment (Multi-TF)")
        elif ema21.iloc[-1] < ema50.iloc[-1] and closes.iloc[-1] < ema200.iloc[-1] and htf_bias == "BEARISH":
            score += 30
            reasons.append("Strong bearish alignment (Multi-TF)")

        # Momentum
        if macd_hist.iloc[-1] > 0 and macd_hist.iloc[-2] < macd_hist.iloc[-1]:
            score += 18
            reasons.append("MACD bullish crossover")
        elif macd_hist.iloc[-1] < 0 and macd_hist.iloc[-2] > macd_hist.iloc[-1]:
            score += 18
            reasons.append("MACD bearish crossover")

        # RSI + Divergence (light)
        if (htf_bias == "BULLISH" and latest_rsi > 48) or (htf_bias == "BEARISH" and latest_rsi < 52):
            score += 12

        # SMC Elements
        if liquidity_sweep != "NONE":
            score += 15
            reasons.append(f"Liquidity sweep: {liquidity_sweep}")
        if fvg != "NONE":
            score += 10
            reasons.append(f"Fair Value Gap: {fvg}")
        if bos != "NONE":
            score += 12
            reasons.append(f"Break of Structure: {bos}")

        if is_strong_candle:
            score += 8

        if latest_adx > 23:
            score += 10
            reasons.append("Trending market")

        early_entry = False

        # Bullish continuation
        if (
            htf_bias == "BULLISH"
            and closes.iloc[-1] > ema21.iloc[-1]
            and closes.iloc[-2] < ema21.iloc[-2]
        ):

            early_entry = True
            score += 12
            reasons.append("Early bullish continuation")

        # Bearish continuation
        elif (
           htf_bias == "BEARISH"
           and closes.iloc[-1] < ema21.iloc[-1]
           and closes.iloc[-2] > ema21.iloc[-2]
        ):

           early_entry = True
           score += 12
           reasons.append("Early bearish continuation")   

        # ====================== SIGNAL DECISION ======================
        signal = "WAIT"
        confidence = min(92, int(score))

        if score >= 58 and htf_bias == "BULLISH" and latest_rsi < 78:   # Lower threshold = more trades
            signal = "BUY"
            reasons.append("✅ Bullish SMC Setup")
        elif score >= 58 and htf_bias == "BEARISH" and latest_rsi > 22:
            signal = "SELL"
            reasons.append("✅ Bearish SMC Setup")

        # ====================== DYNAMIC RISK MANAGEMENT ======================
        stop_loss = None
        take_profit = None
        rr_ratio = 1.8

        if signal == "BUY":
            stop_loss = current_price - (latest_atr * 1.4)
            risk = current_price - stop_loss
            take_profit = current_price + (risk * rr_ratio)
        elif signal == "SELL":
            stop_loss = current_price + (latest_atr * 1.4)
            risk = stop_loss - current_price
            take_profit = current_price - (risk * rr_ratio)

        # Final flexible filter
        if rr_ratio < 1.6 and signal != "WAIT":
            confidence = max(55, confidence - 10)  # Still allow but note risk

        return {
            "signal": signal,
            "trend": htf_bias,
            "confidence": confidence,
            "reason": " | ".join(reasons[:4]),
            "current_price": round(current_price, 4 if current_price < 10 else 2),
            "rsi": round(latest_rsi, 2),
            "adx": round(latest_adx, 2),
            "atr": round(latest_atr, 5),
            "stop_loss": round(stop_loss, 4 if stop_loss and stop_loss < 10 else 2) if stop_loss else None,
            "take_profit": round(take_profit, 4 if take_profit and take_profit < 10 else 2) if take_profit else None,
            "rr_ratio": round(rr_ratio, 2),
            "liquidity_sweep": liquidity_sweep,
            "fvg": fvg,
            "bos": bos,
            "structure": "TRENDING" if latest_adx > 22 else "RANGING",
        }