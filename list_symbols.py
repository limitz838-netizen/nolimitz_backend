import MetaTrader5 as mt5

mt5.initialize()

symbols = mt5.symbols_get()

for s in symbols:
    if "XAU" in s.name.upper():
        print(s.name)

mt5.shutdown()