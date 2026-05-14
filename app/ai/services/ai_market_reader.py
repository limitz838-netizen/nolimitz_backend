import MetaTrader5 as mt5
from datetime import datetime


class AIMarketReader:

    def __init__(self):

        if not mt5.initialize():
            raise Exception("MT5 initialization failed")


    def get_candles(
        self,
        symbol="XAUUSD",
        timeframe=mt5.TIMEFRAME_M5,
        count=100
    ):

        rates = mt5.copy_rates_from_pos(
            symbol,
            timeframe,
            0,
            count
        )

        if rates is None:
            return []

        candles = []

        for rate in rates:

            candles.append({
                "time": datetime.fromtimestamp(int(rate["time"])).isoformat(),
                "open": float(rate["open"]),
                "high": float(rate["high"]),
                "low": float(rate["low"]),
                "close": float(rate["close"]),
                "tick_volume": int(rate["tick_volume"])
            })

        return candles