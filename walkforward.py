"""
================================================================================
  NOLIMITZ — WALK-FORWARD VALIDATION  (read-only, trades nothing)
================================================================================
  WHY THIS EXISTS
  ---------------
  The sweep found gold config "bracket sl=0.7ATR tp=1.4ATR" at +$6,684 / PF 1.09.
  But it found that by testing 20 configs on ONE dataset and picking the best —
  that's selection bias. Some of that profit is the config fitting the NOISE in
  that specific stretch of history, not a real edge.

  This script does the honest test:
    1. Split history into TRAIN (older) and TEST (newer, never used to choose).
    2. Pick the best config using TRAIN ONLY.
    3. Run that winner on TEST — data it has never seen.
    4. Compare. If TEST holds up, the edge is probably real. If TEST collapses,
       the config was curve-fitted and would have lost you money live.

  Also ~20x FASTER than param_sweep.py: entry signals are computed ONCE and
  reused across every config (the entries never change — only the exits do).

  RUN (trader stopped, shares the terminal):
      $env:MT5_TERMINAL_PATH = "D:\\TraderMT5\\terminal64.exe"
      py -3.10 walkforward.py

  Reads real broker spread + tick economics. Writes nothing. Trades nothing.
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
import param_sweep as SW


# ==============================================================================
# SPEED: compute entry signals ONCE per symbol, reuse for every config.
# The entries depend only on price history — never on exit parameters — so
# recomputing them 20x (as param_sweep did) was pure waste.
# ==============================================================================
def find_all_entries(sym_class, m1, m5_time_index, m15, m15_close_times):
    """Returns list of (bar_index, direction, atr15) for every entry signal."""
    # Pre-index ATR by walking m15 forward once instead of scanning per bar.
    entries = []
    m15_ptr = 0
    n15 = len(m15_close_times)
    cur_atr = 0.0

    i = 20
    N = len(m1)
    while i < N - 2:
        t = m1[i]["time"]
        # advance the m15 pointer to the last candle closed at/before t
        while m15_ptr + 1 < n15 and m15_close_times[m15_ptr + 1][0] <= t:
            m15_ptr += 1
            j = m15_close_times[m15_ptr][1]
            if j >= H.BT.ATR_PERIOD + 1:
                cur_atr = H._atr_from(
                    m15[max(0, j - H.BT.ATR_PERIOD - 1): j + 1], H.BT.ATR_PERIOD)
        if cur_atr > 0:
            sig = H.entry_signal(sym_class, m1, i, m5_time_index, cur_atr)
            if sig is not None:
                entries.append((i, sig, cur_atr))
                i += 3          # same spacing as the sweep (cooldown proxy)
                continue
        i += 1
    return entries


def run_config_on_entries(sym_class, m1, entries, P, lo=None, hi=None):
    """
    Replay a config over precomputed entries. lo/hi bound the bar index range
    (for train/test splitting). Returns stats dict or None.
    """
    trades = []
    for (idx, direction, atr15) in entries:
        if lo is not None and idx < lo: continue
        if hi is not None and idx >= hi: continue
        pnl = SW.simulate_trade_params(sym_class, direction, idx, m1, atr15, P)
        trades.append(pnl)

    if len(trades) < 30:
        return None
    n = len(trades)
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    net = sum(trades)
    gw = sum(wins); gl = -sum(losses)
    pf = (gw / gl) if gl > 0 else float('inf')
    eq = peak = dd = 0.0
    for t in trades:
        eq += t; peak = max(peak, eq); dd = max(dd, peak - eq)
    return {
        "n": n, "win_rate": 100*len(wins)/n, "net": net, "pf": pf,
        "avg_win": (gw/len(wins)) if wins else 0,
        "avg_loss": (gl/len(losses)) if losses else 0,
        "max_dd": dd, "expectancy": net/n,
    }


def fmt(r):
    if not r: return "  (too few trades)"
    return (f"n={r['n']:<6} win={r['win_rate']:>5.1f}%  net=${r['net']:>+8.0f}  "
            f"PF={r['pf']:.2f}  avgW=${r['avg_win']:.2f} avgL=${r['avg_loss']:.2f}  "
            f"maxDD=${r['max_dd']:.0f}")


def validate_symbol(base, split=0.6):
    broker_sym = H.find_broker_symbol(base)
    if not broker_sym:
        print(f"\n{base}: not found — skipping"); return
    mt5.symbol_select(broker_sym, True)
    sym_class = H.classify(broker_sym)

    info = mt5.symbol_info(broker_sym)
    try:
        tv, ts = info.trade_tick_value, info.trade_tick_size
        if tv and ts and ts > 0:
            H._USD_PER_MOVE_001[sym_class] = (tv / ts) * 0.01
    except Exception:
        pass

    m1  = H.load(broker_sym, mt5.TIMEFRAME_M1,  H.BT.M1_BARS)
    m5  = H.load(broker_sym, mt5.TIMEFRAME_M5,  H.BT.M5_BARS)
    m15 = H.load(broker_sym, mt5.TIMEFRAME_M15, H.BT.M1_BARS // 15 + 100)
    if len(m1) < 2000 or len(m5) < 200 or len(m15) < 100:
        print(f"\n{broker_sym}: not enough history — skipping"); return

    H.m5_by_time_closes = [c["close"] for c in m5]
    m5_time_index   = [(m5[j]["time"] + 5*60, j) for j in range(len(m5))]
    m15_close_times = [(m15[j]["time"] + 15*60, j) for j in range(len(m15))]

    print(f"\n{'='*78}")
    print(f"  {broker_sym} ({sym_class})  —  WALK-FORWARD VALIDATION")
    print(f"{'='*78}")

    print("  computing entry signals once ...", end=" ", flush=True)
    entries = find_all_entries(sym_class, m1, m5_time_index, m15, m15_close_times)
    print(f"{len(entries)} entries found")

    split_idx = int(len(m1) * split)
    t_split = datetime.fromtimestamp(m1[split_idx]["time"], tz=timezone.utc)
    t0 = datetime.fromtimestamp(m1[0]["time"], tz=timezone.utc)
    t1 = datetime.fromtimestamp(m1[-1]["time"], tz=timezone.utc)
    print(f"  TRAIN: {t0:%Y-%m-%d} → {t_split:%Y-%m-%d}   "
          f"TEST: {t_split:%Y-%m-%d} → {t1:%Y-%m-%d}")

    grid = SW.build_grid()

    # ---- 1) Choose the best config using TRAIN ONLY ----
    train_rows = []
    for P in grid:
        r = run_config_on_entries(sym_class, m1, entries, P, lo=0, hi=split_idx)
        if r: train_rows.append((P, r))
    if not train_rows:
        print("  not enough trades in TRAIN — skipping"); return
    train_rows.sort(key=lambda x: -x[1]["net"])

    print(f"\n  --- TRAIN results (top 5) — this is what we'd have chosen ---")
    for P, r in train_rows[:5]:
        print(f"    {P['label']:<42} {fmt(r)}")

    best_P, best_train = train_rows[0]

    # ---- 2) Run that winner on TEST (never seen) ----
    test_r = run_config_on_entries(sym_class, m1, entries, best_P,
                                   lo=split_idx, hi=None)

    print(f"\n  --- OUT-OF-SAMPLE TEST of the TRAIN winner ---")
    print(f"    config: {best_P['label']}")
    print(f"    TRAIN:  {fmt(best_train)}")
    print(f"    TEST:   {fmt(test_r)}")

    # ---- 3) Verdict ----
    print(f"\n  {'-'*74}")
    if not test_r:
        print("  ⚠️  Too few trades out-of-sample to judge. Need more history.")
    elif test_r["net"] > 0 and test_r["pf"] > 1.05:
        degradation = (best_train["pf"] - test_r["pf"])
        print(f"  🟢 HOLDS UP OUT-OF-SAMPLE. PF {best_train['pf']:.2f} (train) → "
              f"{test_r['pf']:.2f} (test).")
        print(f"     The edge survived on data it was never tuned on. This is the")
        print(f"     strongest evidence you can get from history alone.")
        print(f"     NEXT: demo forward-test this exact config for 2-4 weeks before")
        print(f"     ANY live client money. Backtest ≠ live execution.")
    elif test_r["net"] > 0:
        print(f"  🟡 WEAKLY positive out-of-sample (PF {test_r['pf']:.2f}). The edge")
        print(f"     shrank from train ({best_train['pf']:.2f}) — some of it was")
        print(f"     curve-fitting. Too thin to trust with client money yet.")
    else:
        print(f"  🔴 FAILED OUT-OF-SAMPLE. Train said +${best_train['net']:.0f}, "
              f"test says ${test_r['net']:+.0f}.")
        print(f"     The config was CURVE-FITTED to the training period. Deploying")
        print(f"     it would have lost money. This is exactly what this test is for.")
    print(f"  {'-'*74}")

    # ---- 4) Also show how the CURRENT LIVE config does out-of-sample ----
    live_P = {
        "label": "CURRENT LIVE (scaleout sl=1.0ATR tp=1.6ATR)",
        "sl_atr": 1.0, "tp_atr": 1.6,
        "scale1_usd": 2.0, "scale2_usd": 4.0, "scale2_lock": 2.0,
        "runner_trail": 2.0,
    }
    live_test = run_config_on_entries(sym_class, m1, entries, live_P,
                                      lo=split_idx, hi=None)
    print(f"\n  For comparison — what you're running LIVE right now, on TEST data:")
    print(f"    {live_P['label']}")
    print(f"    TEST:   {fmt(live_test)}")


def main():
    term = os.environ.get("MT5_TERMINAL_PATH")
    ok = mt5.initialize(path=term) if term else mt5.initialize()
    if not ok:
        print(f"MT5 init failed: {mt5.last_error()}"); sys.exit(1)

    print("="*78)
    print("  NOLIMITZ WALK-FORWARD VALIDATION")
    print("  Picks the best config on OLD data, then tests it on NEW unseen data.")
    print("  This is the test that catches curve-fitting.")
    print("="*78)

    for base in ("XAUUSD", "BTCUSD"):
        try:
            validate_symbol(base)
        except Exception as ex:
            print(f"\n{base}: error {ex}")
            import traceback; traceback.print_exc()

    print("\n" + "#"*78)
    print("  A 🟢 here means: worth a DEMO forward-test. It does NOT mean deploy.")
    print("  Live execution (spread spikes, slippage, latency) is always worse")
    print("  than backtest. Your live gold win rate was 47% where backtest said")
    print("  79% — that gap is your real-world execution cost. Budget for it.")
    print("#"*78)
    mt5.shutdown()


if __name__ == "__main__":
    main()