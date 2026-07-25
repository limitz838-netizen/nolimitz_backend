"""
================================================================================
  NOLIMITZ — M15 HIGHER-TIMEFRAME STRATEGIES + BUILT-IN WALK-FORWARD
================================================================================
  WHY M15
  -------
  The M1 scalper failed validation: targets ~$2 while spread+slippage+latency
  eat 5-10% of every trade, on the most arbitraged timeframe there is. On M15,
  targets are $10-40, the same costs are <1%, and the 1.5s Nairobi→Oregon
  latency stops mattering at all.

  TWO ENTRY MODELS — each with a CAUSAL story, not an indicator crossing:

  A) LONDON-OPEN BREAKOUT (gold only)
     Mechanism: real institutional order flow arrives at the London open.
     Setup: measure the Asian-session range (00:00–07:00 UTC). When an M15
     candle CLOSES beyond that range between 07:00–10:00 UTC, enter with the
     breakout. One trade per session. Stop: other side of the range (capped
     at 1.5 ATR). Target: R multiple of the risk. Flat by 20:00 UTC.

  B) PRIOR-DAY SWEEP REJECTION (gold + BTC)
     Mechanism: stops cluster beyond the prior day's high/low; sweeps of
     those levels followed by rejection are a real structural phenomenon.
     Setup: an M15 bar trades THROUGH the prior day's high but CLOSES back
     below it → SELL (mirror for the low → BUY). Stop: beyond the sweep
     extreme + 0.2 ATR. Target: R multiple. Max one trade per level per day.

  HONESTY BUILT IN
  ----------------
  • Walk-forward is INSIDE this script: the small grid is chosen on TRAIN
    (older 60%) and judged on TEST (newer 40%, never used to choose).
  • Grids are deliberately SMALL (3-4 configs each) to limit selection bias.
  • Spread deducted per trade; worst-case intra-bar ordering (stop first).
  • M15 gives far fewer trades than M1 — the script prints the sample size
    and warns when it's too small to trust.

  RUN (trader stopped, shares the terminal):
      $env:MT5_TERMINAL_PATH = "D:\\TraderMT5\\terminal64.exe"
      py -3.10 htf_strategies.py
================================================================================
"""

import os, sys
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
except Exception:
    print("MetaTrader5 not importable — run on the Windows box.")
    sys.exit(1)

import backtest_harness as H


M15_BARS = 120_000            # ask for years of M15 if the broker has it

# Session hours (UTC). Gold's liquidity clock.
ASIA_START, ASIA_END = 0, 7   # Asian range 00:00–06:59
LON_OPEN, LON_CUTOFF = 7, 10  # take breakouts 07:00–09:59
FLAT_HOUR = 20                # force-flat time for session trades


# ==============================================================================
# DATA PREP
# ==============================================================================
def prep(broker_sym):
    m15 = H.load(broker_sym, mt5.TIMEFRAME_M15, M15_BARS)
    if len(m15) < 3000:
        return None
    # attach UTC datetime + day key to every bar
    for b in m15:
        dt = datetime.fromtimestamp(b["time"], tz=timezone.utc)
        b["dt"] = dt
        b["day"] = dt.strftime("%Y-%m-%d")
        b["hour"] = dt.hour
    # ATR series (M15, 14) — value AT each bar uses bars up to and incl. it
    atr = [0.0] * len(m15)
    period = 14
    for i in range(period + 1, len(m15)):
        trs = []
        for k in range(i - period + 1, i + 1):
            h_, l_ = m15[k]["high"], m15[k]["low"]
            pc = m15[k - 1]["close"]
            trs.append(max(h_ - l_, abs(h_ - pc), abs(l_ - pc)))
        atr[i] = sum(trs) / period
    # prior-day high/low per day
    day_hi, day_lo = {}, {}
    for b in m15:
        d = b["day"]
        day_hi[d] = max(day_hi.get(d, -1e18), b["high"])
        day_lo[d] = min(day_lo.get(d, 1e18), b["low"])
    days = sorted(day_hi)
    prev_of = {days[i]: days[i - 1] for i in range(1, len(days))}
    return m15, atr, day_hi, day_lo, prev_of


# ==============================================================================
# ENTRY DETECTION (per strategy) — returns list of trade specs
# Each spec: dict(idx, dir, stop_price, atr) — target set by config at runtime.
# ==============================================================================
def entries_london_breakout(m15, atr):
    out = []
    cur_day = None
    asia_hi = asia_lo = None
    taken_today = False
    for i, b in enumerate(m15):
        if b["day"] != cur_day:
            cur_day = b["day"]
            asia_hi, asia_lo = None, None
            taken_today = False
        h = b["hour"]
        if ASIA_START <= h < ASIA_END:
            asia_hi = b["high"] if asia_hi is None else max(asia_hi, b["high"])
            asia_lo = b["low"] if asia_lo is None else min(asia_lo, b["low"])
            continue
        if taken_today or asia_hi is None or not (LON_OPEN <= h < LON_CUTOFF):
            continue
        a = atr[i]
        if a <= 0:
            continue
        rng = asia_hi - asia_lo
        if rng <= 0 or rng > a * 6:       # skip absurd ranges (gaps/holidays)
            continue
        if b["close"] > asia_hi:          # bullish breakout close
            stop = max(asia_lo, b["close"] - 1.5 * a)
            out.append({"idx": i, "dir": "BUY", "stop": stop, "atr": a,
                        "session": True})
            taken_today = True
        elif b["close"] < asia_lo:
            stop = min(asia_hi, b["close"] + 1.5 * a)
            out.append({"idx": i, "dir": "SELL", "stop": stop, "atr": a,
                        "session": True})
            taken_today = True
    return out


def entries_sweep_rejection(m15, atr, day_hi, day_lo, prev_of):
    out = []
    used = set()      # (day, "HI"/"LO") — one trade per level per day
    for i, b in enumerate(m15):
        pd = prev_of.get(b["day"])
        if pd is None:
            continue
        a = atr[i]
        if a <= 0:
            continue
        ph, pl = day_hi[pd], day_lo[pd]
        # Sweep of prior-day HIGH, close back below → SELL
        if (b["day"], "HI") not in used and b["high"] > ph and b["close"] < ph:
            out.append({"idx": i, "dir": "SELL",
                        "stop": b["high"] + 0.2 * a, "atr": a,
                        "session": False})
            used.add((b["day"], "HI"))
        # Sweep of prior-day LOW, close back above → BUY
        if (b["day"], "LO") not in used and b["low"] < pl and b["close"] > pl:
            out.append({"idx": i, "dir": "BUY",
                        "stop": b["low"] - 0.2 * a, "atr": a,
                        "session": False})
            used.add((b["day"], "LO"))
    return out


# ==============================================================================
# SIMULATION — bracket with worst-case ordering; session trades flat by 20:00
# ==============================================================================
def simulate(sym_class, m15, spec, R):
    i = spec["idx"]
    is_buy = (spec["dir"] == "BUY")
    entry = m15[i]["close"]
    stop = spec["stop"]
    risk = (entry - stop) if is_buy else (stop - entry)
    if risk <= 0:
        return None
    target = entry + risk * R if is_buy else entry - risk * R

    upm = H.usd_per_price_unit(sym_class, entry)
    spread = H.BT.SPREAD_USD_PER_001[sym_class]

    for j in range(i + 1, len(m15)):
        b = m15[j]
        # session trades: force flat at FLAT_HOUR or on day change
        if spec["session"] and (b["day"] != m15[i]["day"] or b["hour"] >= FLAT_HOUR):
            move = (b["open"] - entry) if is_buy else (entry - b["open"])
            return move * upm - spread
        adverse = b["low"] if is_buy else b["high"]
        favor   = b["high"] if is_buy else b["low"]
        hit_stop = (adverse <= stop) if is_buy else (adverse >= stop)
        if hit_stop:                       # pessimistic: stop first
            move = (stop - entry) if is_buy else (entry - stop)
            return move * upm - spread
        hit_tp = (favor >= target) if is_buy else (favor <= target)
        if hit_tp:
            move = (target - entry) if is_buy else (entry - target)
            return move * upm - spread
    # data ended with trade open — mark at last close
    last = m15[-1]["close"]
    move = (last - entry) if is_buy else (entry - last)
    return move * upm - spread


def stats(trades):
    if len(trades) < 20:
        return None
    n = len(trades)
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    net = sum(trades)
    gw, gl = sum(wins), -sum(losses)
    pf = (gw / gl) if gl > 0 else float("inf")
    eq = pk = dd = 0.0
    for t in trades:
        eq += t; pk = max(pk, eq); dd = max(dd, pk - eq)
    return {"n": n, "wr": 100 * len(wins) / n, "net": net, "pf": pf,
            "aw": (gw / len(wins)) if wins else 0,
            "al": (gl / len(losses)) if losses else 0, "dd": dd}


def fmt(r):
    if not r:
        return "(too few trades to judge — under 20)"
    pf = f"{r['pf']:.2f}" if r["pf"] != float("inf") else "inf"
    return (f"n={r['n']:<5} win={r['wr']:>5.1f}%  net=${r['net']:>+8.0f}  "
            f"PF={pf}  avgW=${r['aw']:.2f} avgL=${r['al']:.2f}  maxDD=${r['dd']:.0f}")


# ==============================================================================
# WALK-FORWARD RUN OF ONE STRATEGY ON ONE SYMBOL
# ==============================================================================
def run_strategy(name, sym_class, m15, specs, R_grid, split=0.6):
    if not specs:
        print(f"    {name}: no entries found"); return
    split_idx = int(len(m15) * split)
    train = [s for s in specs if s["idx"] < split_idx]
    test  = [s for s in specs if s["idx"] >= split_idx]
    t0 = m15[0]["dt"]; ts = m15[split_idx]["dt"]; t1 = m15[-1]["dt"]

    print(f"\n  ── {name} ──")
    print(f"    entries: {len(specs)} total  ({len(train)} train / {len(test)} test)")
    print(f"    TRAIN {t0:%Y-%m-%d} → {ts:%Y-%m-%d}   TEST {ts:%Y-%m-%d} → {t1:%Y-%m-%d}")

    # choose R on TRAIN only (tiny grid — deliberate)
    rows = []
    for R in R_grid:
        r = stats([p for p in (simulate(sym_class, m15, s, R) for s in train)
                   if p is not None])
        if r:
            rows.append((R, r))
    if not rows:
        print("    ⚠️  not enough TRAIN trades to choose a target — skipping")
        return
    rows.sort(key=lambda x: -x[1]["net"])
    for R, r in rows:
        print(f"    train R={R:<4} {fmt(r)}")
    best_R, best_train = rows[0]

    test_r = stats([p for p in (simulate(sym_class, m15, s, best_R) for s in test)
                    if p is not None])
    print(f"    ── out-of-sample (R={best_R}) ──")
    print(f"    TEST      {fmt(test_r)}")

    if not test_r:
        print("    ⚠️  too few TEST trades to judge — need more history.")
    elif (test_r["net"] > 0 and test_r["pf"] > 1.1
          and best_train["net"] > 0 and best_train["pf"] > 1.1):
        print(f"    🟢 HOLDS UP out-of-sample (PF {best_train['pf']:.2f} train → "
              f"{test_r['pf']:.2f} test). Worth a DEMO forward-test.")
    elif test_r["net"] > 0 and best_train["net"] <= 0:
        print(f"    🟡 TEST positive but TRAIN was negative — that's luck, not "
              f"edge. Not actionable.")
    elif test_r["net"] > 0:
        print(f"    🟡 Weakly positive out-of-sample — thin, treat with caution.")
    else:
        print(f"    🔴 Fails out-of-sample. This entry idea has no edge here.")


def main():
    term = os.environ.get("MT5_TERMINAL_PATH")
    ok = mt5.initialize(path=term) if term else mt5.initialize()
    if not ok:
        print(f"MT5 init failed: {mt5.last_error()}"); sys.exit(1)

    print("=" * 78)
    print("  NOLIMITZ M15 STRATEGIES — causal-story entries, walk-forward built in")
    print("  (read-only; spread-adjusted; worst-case fills; small grids on purpose)")
    print("=" * 78)

    for base in ("XAUUSD", "BTCUSD"):
        broker_sym = H.find_broker_symbol(base)
        if not broker_sym:
            print(f"\n{base}: not found — skipping"); continue
        mt5.symbol_select(broker_sym, True)
        sym_class = H.classify(broker_sym)
        info = mt5.symbol_info(broker_sym)
        try:
            tv, ts = info.trade_tick_value, info.trade_tick_size
            if tv and ts and ts > 0:
                H._USD_PER_MOVE_001[sym_class] = (tv / ts) * 0.01
        except Exception:
            pass

        prepped = prep(broker_sym)
        if not prepped:
            print(f"\n{broker_sym}: not enough M15 history — skipping"); continue
        m15, atr, day_hi, day_lo, prev_of = prepped

        print(f"\n{'='*78}\n  {broker_sym} ({sym_class})  "
              f"{m15[0]['dt']:%Y-%m-%d} → {m15[-1]['dt']:%Y-%m-%d}  "
              f"({len(m15)} M15 bars)  spread=${H.BT.SPREAD_USD_PER_001[sym_class]:.2f}"
              f"\n{'='*78}")

        # A) London breakout — gold only (sessions mean nothing on 24/7 BTC)
        if sym_class == "GOLD":
            specs = entries_london_breakout(m15, atr)
            run_strategy("LONDON-OPEN BREAKOUT", sym_class, m15, specs,
                         R_grid=(1.5, 2.0, 3.0))

        # B) Prior-day sweep rejection — both symbols
        specs = entries_sweep_rejection(m15, atr, day_hi, day_lo, prev_of)
        run_strategy("PRIOR-DAY SWEEP REJECTION", sym_class, m15, specs,
                     R_grid=(1.5, 2.0, 3.0))

    print("\n" + "#" * 78)
    print("  Read it straight: 🟢 = earns a demo forward-test (2-4 weeks) before")
    print("  any client money. 🔴 = the idea is dead here; test a different one.")
    print("  Small trade counts (M15 trades are rare) mean wider uncertainty —")
    print("  a 🟢 on 40 trades is a hint, not proof.")
    print("#" * 78)
    mt5.shutdown()


if __name__ == "__main__":
    main()