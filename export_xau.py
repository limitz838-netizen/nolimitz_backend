import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta

if not mt5.initialize():
    print("MT5 connection failed:", mt5.last_error())
    quit()

symbol = "XAUUSD"

end_date = datetime.now()
start_date = end_date - timedelta(days=365)

print(f"Downloading {symbol} M1 data...")

rates = mt5.copy_rates_range(
    symbol,
    mt5.TIMEFRAME_M1,
    start_date,
    end_date
)

if rates is None:
    print("No data returned.")
    mt5.shutdown()
    quit()

df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s")

filename = f"{symbol}_M1_1YEAR.csv"
df.to_csv(filename, index=False)

print(f"Saved: {filename}")
print(f"Rows: {len(df)}")

mt5.shutdown()