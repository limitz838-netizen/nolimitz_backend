"""
================================================================================
  NOLIMITZ — SPREAD READER  (read-only)
================================================================================
  Reads the LIVE bid/ask + contract economics for gold and BTC straight from
  your MT5 terminal, and prints the exact SPREAD_USD_PER_001 values to paste
  into backtest_harness.py. No guessing, no manual math.

  RUN:
      $env:MT5_TERMINAL_PATH = "D:\\TraderMT5\\terminal64.exe"
      py -3.10 read_spread.py

  (Stop the trader first if it shares that terminal.)

  Spread on these instruments moves during the day (wider at rollover / news).
  This samples a few times so you get a typical figure, not a one-off spike.
================================================================================
"""

import os, sys, time

try:
    import MetaTrader5 as mt5
except Exception:
    print("MetaTrader5 not importable — run on the Windows box.")
    sys.exit(1)


def find_symbol(base):
    b = base.upper().replace(" ", "")
    cands = [b]
    if "BTC" in b: cands += ["BTCUSD", "BTC", "BITCOIN", "BTCUSDT"]
    if "XAU" in b: cands += ["XAUUSD", "GOLD", "XAUUSDm", "XAUUSDc"]
    allsyms = mt5.symbols_get() or []
    for c in cands:
        for s in allsyms:
            if s.name.upper().replace(" ", "") == c:
                return s.name
    for s in allsyms:
        n = s.name.upper().replace(" ", "")
        for c in cands:
            if c and c in n and len(c) >= 3:
                return s.name
    return None


def usd_per_price_move_001(info, price):
    """
    USD change per 1.0 price move, for a 0.01 lot.
    Uses the broker's own tick economics so it's correct for any symbol:
      value_per_point = trade_tick_value / trade_tick_size
      for 0.01 lot:    value_per_point * 0.01
    Falls back to the known gold/BTC values if the broker doesn't expose ticks.
    """
    try:
        tv = info.trade_tick_value
        ts = info.trade_tick_size
        if tv and ts and ts > 0:
            per_point_per_lot = tv / ts            # USD per 1.0 move, per 1.0 lot
            return per_point_per_lot * 0.01        # per 0.01 lot
    except Exception:
        pass
    # Fallback (standard contracts): gold 0.01lot => $1 per $1 move;
    # BTC 0.01lot => $0.01 per $1 move (0.01 BTC).
    name = (info.name if info else "").upper()
    if "XAU" in name or "GOLD" in name:
        return 1.0
    if "BTC" in name:
        return 0.10
    return 1.0


def measure(base, samples=6, gap=1.0):
    sym = find_symbol(base)
    if not sym:
        print(f"  {base}: not found on this broker")
        return None
    mt5.symbol_select(sym, True)
    info = mt5.symbol_info(sym)
    if not info:
        print(f"  {sym}: no symbol_info")
        return None

    spreads_price = []
    for _ in range(samples):
        t = mt5.symbol_info_tick(sym)
        if t and t.ask > 0 and t.bid > 0:
            spreads_price.append(t.ask - t.bid)
        time.sleep(gap)
    if not spreads_price:
        print(f"  {sym}: no live ticks (market closed?)")
        return None

    price = mt5.symbol_info_tick(sym).bid
    usd_per_move = usd_per_price_move_001(info, price)

    med_price = sorted(spreads_price)[len(spreads_price)//2]
    min_price = min(spreads_price)
    max_price = max(spreads_price)

    med_usd = med_price * usd_per_move
    min_usd = min_price * usd_per_move
    max_usd = max_price * usd_per_move

    cls = "GOLD" if ("XAU" in sym.upper() or "GOLD" in sym.upper()) else \
          "BTC" if "BTC" in sym.upper() else "OTHER"

    print(f"\n  {sym}  ({cls})")
    print(f"    price now:            {price:.2f}")
    print(f"    tick_value/tick_size: {info.trade_tick_value}/{info.trade_tick_size}"
          f"  -> ${usd_per_move:.4f} per 1.0 move per 0.01 lot")
    print(f"    spread (price):       median={med_price:.3f}  min={min_price:.3f}  max={max_price:.3f}")
    print(f"    spread (USD/0.01lot): median=${med_usd:.3f}  min=${min_usd:.3f}  max=${max_usd:.3f}")
    return cls, med_usd, max_usd


def main():
    term = os.environ.get("MT5_TERMINAL_PATH")
    ok = mt5.initialize(path=term) if term else mt5.initialize()
    if not ok:
        print(f"MT5 init failed: {mt5.last_error()} (set MT5_TERMINAL_PATH; "
              f"stop the trader if it shares the terminal)")
        sys.exit(1)

    print("=" * 66)
    print("  LIVE SPREAD READER — sampling a few seconds for a typical value")
    print("=" * 66)

    out = {}
    for base in ("XAUUSD", "BTCUSD"):
        r = measure(base)
        if r:
            cls, med, mx = r
            out[cls] = (med, mx)

    print("\n" + "=" * 66)
    print("  PASTE THIS into backtest_harness.py (use MEDIAN for a fair test,")
    print("  then re-run with MAX to stress-test the worst case):")
    print("=" * 66)
    gold_med = out.get("GOLD", (0.30, 0.30))[0]
    btc_med  = out.get("BTC",  (3.00, 3.00))[0]
    gold_max = out.get("GOLD", (0.30, 0.30))[1]
    btc_max  = out.get("BTC",  (3.00, 3.00))[1]
    print(f'\n  # typical (median) spread:')
    print(f'  SPREAD_USD_PER_001 = {{"GOLD": {gold_med:.2f}, "BTC": {btc_med:.2f}}}')
    print(f'\n  # worst-case (max seen) spread — re-run with this too:')
    print(f'  SPREAD_USD_PER_001 = {{"GOLD": {gold_max:.2f}, "BTC": {btc_max:.2f}}}')
    print("\n  NOTE: if the market is closed right now, spreads read wide/frozen.")
    print("  Run this during active trading hours for a realistic number.")
    print("=" * 66)

    mt5.shutdown()


if __name__ == "__main__":
    main()