"""
================================================================================
  NOLIMITZ — STRATEGY PARAMETER SWEEP  (read-only, trades nothing)
================================================================================
  The backtest proved the CURRENT config loses money despite a 79% gold win
  rate — because avg loss ($8.71) is ~4x avg win ($2.13). The entries are good;
  the risk-reward is upside-down.

  This sweep keeps the EXACT entry logic and re-runs the SAME history over a
  grid of EXIT settings — stop distance, take-profit, and scale-out triggers —
  to answer one question: is there ANY configuration that turns that high win
  rate into a positive profit factor? If yes, it names it. If no, that's
  decisive too — the exits can't save it and the entries themselves need work.

  It reuses backtest_harness.py's data loading + entry_signal so the entries
  stay byte-identical to what we already validated. Only the EXIT model is
  parameterized here.

  RUN (trader stopped, shares the terminal):
      $env:MT5_TERMINAL_PATH = "D:\\TraderMT5\\terminal64.exe"
      py -3.10 param_sweep.py

  Reads real broker spread + tick economics from the harness. Nothing written.
================================================================================
"""

import os, sys, itertools
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
except Exception:
    print("MetaTrader5 not importable — run on the Windows box.")
    sys.exit(1)

# Reuse the validated harness pieces so entries + economics stay identical.
import backtest_harness as H


# ==============================================================================
# PARAMETERIZED EXIT SIMULATION
# Same walk-forward + worst-case-ordering as H.simulate_trade, but the exit
# parameters are passed in so we can sweep them. Kept deliberately close to the
# original so results are comparable.
# ==============================================================================
def simulate_trade_params(sym_class, direction, entry_idx, m1, atr15, P):
    """
    P is a dict of exit parameters:
      sl_atr        : stop distance in ATR units
      tp_atr        : take-profit distance in ATR units (full-close target)
      scale1_usd    : +USD/0.01lot to take 50% and move SL to breakeven (or None)
      scale2_usd    : +USD/0.01lot to take 25% and lock scale2_lock_usd (or None)
      scale2_lock   : USD/0.01lot to lock after scale2
      runner_trail  : USD/0.01lot the runner trails behind peak (or None)
      be_at_usd     : move SL to breakeven once floating >= this USD (or None)
    Set scale triggers to None to test a pure SL/TP bracket (no scaling).
    Returns realized USD P&L per 0.01 lot after spread+commission.
    """
    is_buy = (direction == "BUY")
    entry_price = m1[entry_idx]["close"]
    usd_per_move = H.usd_per_price_unit(sym_class, entry_price)
    price_per_usd = 1.0 / usd_per_move

    sl_dist = min(atr15 * P["sl_atr"], entry_price * H.BT.MAX_SL_PCT_OF_PRICE)
    tp_dist = atr15 * P["tp_atr"]
    if is_buy:
        sl_price = entry_price - sl_dist
        tp_price = entry_price + tp_dist
    else:
        sl_price = entry_price + sl_dist
        tp_price = entry_price - tp_dist

    spread_cost = H.BT.SPREAD_USD_PER_001[sym_class]
    commission  = H.BT.COMMISSION_USD_PER_001[sym_class]

    remaining = 1.0
    banked_usd = 0.0
    scale1_done = scale2_done = False
    be_done = False
    runner_peak_usd = 0.0
    cur_sl = sl_price

    def to_usd(px):
        move = (px - entry_price) if is_buy else (entry_price - px)
        return move * usd_per_move

    for j in range(entry_idx + 1, len(m1)):
        bar = m1[j]
        adverse = bar["low"] if is_buy else bar["high"]
        favor   = bar["high"] if is_buy else bar["low"]

        # Stop first (pessimistic)
        stop_hit = (adverse <= cur_sl) if is_buy else (adverse >= cur_sl)
        if stop_hit:
            return banked_usd + to_usd(cur_sl) * remaining - spread_cost - commission

        best_usd = to_usd(favor)

        # Plain breakeven move (if enabled and no scaling used it yet)
        if P.get("be_at_usd") and not be_done and best_usd >= P["be_at_usd"]:
            new_be = entry_price
            if is_buy: cur_sl = max(cur_sl, new_be)
            else:      cur_sl = min(cur_sl, new_be)
            be_done = True

        # Scale 1
        if P.get("scale1_usd") and not scale1_done and best_usd >= P["scale1_usd"]:
            banked_usd += P["scale1_usd"] * 0.50
            remaining -= 0.50
            scale1_done = True
            if is_buy: cur_sl = max(cur_sl, entry_price)
            else:      cur_sl = min(cur_sl, entry_price)

        # Scale 2
        if (P.get("scale2_usd") and scale1_done and not scale2_done
                and best_usd >= P["scale2_usd"]):
            banked_usd += P["scale2_usd"] * 0.25
            remaining -= 0.25
            scale2_done = True
            lock_move = P.get("scale2_lock", 0) * price_per_usd
            locked = (entry_price + lock_move) if is_buy else (entry_price - lock_move)
            if is_buy: cur_sl = max(cur_sl, locked)
            else:      cur_sl = min(cur_sl, locked)

        # Runner trail (after scale2, if enabled)
        if P.get("runner_trail") and scale2_done:
            if best_usd > runner_peak_usd:
                runner_peak_usd = best_usd
            trail_usd = runner_peak_usd - P["runner_trail"]
            if trail_usd > 0:
                tmove = trail_usd * price_per_usd
                nsl = (entry_price + tmove) if is_buy else (entry_price - tmove)
                if is_buy: cur_sl = max(cur_sl, nsl)
                else:      cur_sl = min(cur_sl, nsl)

        # Full TP (only meaningful when no scaling consumed the position)
        if not scale1_done:
            tp_hit = (favor >= tp_price) if is_buy else (favor <= tp_price)
            if tp_hit:
                return to_usd(tp_price) * remaining - spread_cost - commission

    return banked_usd + to_usd(m1[-1]["close"]) * remaining - spread_cost - commission


# ==============================================================================
# RUN ONE CONFIG OVER A SYMBOL'S HISTORY
# ==============================================================================
def run_config(sym_class, m1, m5_time_index, m15_close_times, m15, P):
    """Returns (n, win_rate, net, pf, avg_win, avg_loss, max_dd)."""
    def atr_asof(t):
        idx = None
        for ct, j in m15_close_times:
            if ct <= t: idx = j
            else: break
        if idx is None or idx < H.BT.ATR_PERIOD + 1:
            return 0.0
        return H._atr_from(m15[max(0, idx - H.BT.ATR_PERIOD - 1): idx + 1], H.BT.ATR_PERIOD)

    trades = []
    i = 20; N = len(m1)
    while i < N - 2:
        atr15 = atr_asof(m1[i]["time"])
        sig = H.entry_signal(sym_class, m1, i, m5_time_index, atr15)
        if sig is None:
            i += 1; continue
        pnl = simulate_trade_params(sym_class, sig, i, m1, atr15, P)
        trades.append(pnl)
        i += 3

    if not trades:
        return None
    n = len(trades)
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    net = sum(trades)
    gw = sum(wins); gl = -sum(losses)
    wr = 100 * len(wins) / n
    pf = (gw / gl) if gl > 0 else float('inf')
    aw = (gw / len(wins)) if wins else 0
    al = (gl / len(losses)) if losses else 0
    eq = peak = dd = 0.0
    for t in trades:
        eq += t; peak = max(peak, eq); dd = max(dd, peak - eq)
    return (n, wr, net, pf, aw, al, dd)


# ==============================================================================
# THE SWEEP GRID
# ==============================================================================
# Each config is a hypothesis about how to fix the upside-down R:R. We test:
#  A) Tighter stops (cut the $8.71 avg loss)
#  B) Simple bracket, no scaling (does scaling even help?)
#  C) Later/higher first-partial (let winners run before banking)
#  D) Wider TP with tight stop (flip the R:R in our favour)
def build_grid():
    grid = []

    # ---- Family A: current-style scale-out, but sweep the STOP tighter ----
    for sl_atr in (1.0, 0.7, 0.5, 0.35):
        for tp_atr in (1.6, 2.0):
            grid.append({
                "label": f"scaleout sl={sl_atr}ATR tp={tp_atr}ATR",
                "sl_atr": sl_atr, "tp_atr": tp_atr,
                "scale1_usd": 2.0, "scale2_usd": 4.0, "scale2_lock": 2.0,
                "runner_trail": 2.0,
            })

    # ---- Family B: plain SL/TP bracket, NO scaling — pure R:R test ----
    for sl_atr, tp_atr in [(0.5, 0.5), (0.5, 0.75), (0.5, 1.0),
                            (0.35, 0.5), (0.35, 0.7), (0.7, 1.4),
                            (1.0, 1.5), (0.5, 1.5)]:
        grid.append({
            "label": f"bracket sl={sl_atr}ATR tp={tp_atr}ATR (R:R {tp_atr/sl_atr:.1f})",
            "sl_atr": sl_atr, "tp_atr": tp_atr,
            "scale1_usd": None, "scale2_usd": None, "runner_trail": None,
            "be_at_usd": None,
        })

    # ---- Family C: bracket + early breakeven (cut losers, keep winners) ----
    for sl_atr, tp_atr, be in [(0.7, 2.0, 1.0), (0.7, 2.5, 1.5),
                               (0.5, 2.0, 1.0), (1.0, 3.0, 2.0)]:
        grid.append({
            "label": f"bracket+BE sl={sl_atr} tp={tp_atr} be@${be}",
            "sl_atr": sl_atr, "tp_atr": tp_atr,
            "scale1_usd": None, "scale2_usd": None, "runner_trail": None,
            "be_at_usd": be,
        })

    return grid


def main():
    term = os.environ.get("MT5_TERMINAL_PATH")
    ok = mt5.initialize(path=term) if term else mt5.initialize()
    if not ok:
        print(f"MT5 init failed: {mt5.last_error()} (set MT5_TERMINAL_PATH; "
              f"stop the trader if it shares the terminal)")
        sys.exit(1)

    print("=" * 78)
    print("  NOLIMITZ PARAMETER SWEEP — same entries, different exits")
    print("  Goal: find an exit config that turns the high win rate into profit.")
    print("=" * 78)

    grid = build_grid()

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

        m1 = H.load(broker_sym, mt5.TIMEFRAME_M1, H.BT.M1_BARS)
        m5 = H.load(broker_sym, mt5.TIMEFRAME_M5, H.BT.M5_BARS)
        m15 = H.load(broker_sym, mt5.TIMEFRAME_M15, H.BT.M1_BARS // 15 + 100)
        if len(m1) < 1000 or len(m5) < 200 or len(m15) < 100:
            print(f"\n{broker_sym}: not enough history — skipping"); continue

        H.m5_by_time_closes = [c["close"] for c in m5]
        m5_time_index = [(m5[j]["time"] + 5*60, j) for j in range(len(m5))]
        m15_close_times = [(m15[j]["time"] + 15*60, j) for j in range(len(m15))]

        s = datetime.fromtimestamp(m1[0]["time"], tz=timezone.utc)
        e = datetime.fromtimestamp(m1[-1]["time"], tz=timezone.utc)
        print(f"\n{'='*78}\n  {broker_sym} ({sym_class})  {s:%Y-%m-%d} → {e:%Y-%m-%d}  "
              f"spread=${H.BT.SPREAD_USD_PER_001[sym_class]:.2f}\n{'='*78}")
        print(f"  {'config':<44}{'trades':>7}{'win%':>7}{'net$':>10}{'PF':>6}")
        print("  " + "-"*74)

        rows = []
        for P in grid:
            r = run_config(sym_class, m1, m5_time_index, m15_close_times, m15, P)
            if r is None:
                continue
            n, wr, net, pf, aw, al, dd = r
            rows.append((P["label"], n, wr, net, pf, aw, al, dd))

        # Sort best net P&L first
        rows.sort(key=lambda x: -x[3])
        for label, n, wr, net, pf, aw, al, dd in rows:
            flag = "🟢" if net > 0 and pf > 1.05 else ("🟡" if net > 0 else "🔴")
            pf_s = f"{pf:.2f}" if pf != float('inf') else "inf"
            print(f"  {flag} {label:<41}{n:>7}{wr:>6.1f}%{net:>+10.0f}{pf_s:>6}")

        # Highlight the winner
        best = rows[0] if rows else None
        if best and best[3] > 0 and best[4] > 1.05:
            print(f"\n  🏆 BEST for {sym_class}: {best[0]}")
            print(f"     net ${best[3]:+.0f} | win {best[2]:.1f}% | PF {best[4]:.2f} | "
                  f"avg win ${best[5]:.2f} vs avg loss ${best[6]:.2f} | maxDD ${best[7]:.0f}")
            print(f"     → worth a demo forward-test with THIS exit config.")
        else:
            print(f"\n  ❌ No profitable exit config found for {sym_class}. "
                  f"The ENTRIES need work, not just the exits.")

    print("\n" + "#"*78)
    print("  Reminder: a 🟢 here is HISTORY saying 'worth a demo test', not")
    print("  'deploy to clients'. A profitable backtest config must still prove")
    print("  itself forward on a demo account before real money.")
    print("#"*78)
    mt5.shutdown()


if __name__ == "__main__":
    main()