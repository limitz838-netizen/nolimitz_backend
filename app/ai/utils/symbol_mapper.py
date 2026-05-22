def find_broker_symbol(mt5, target_symbol):
    symbols = mt5.symbols_get()

    target = target_symbol.upper()

    for s in symbols:
        name = s.name.upper()

        if target in name:
            return s.name

    return None