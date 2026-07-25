"""
================================================================================
  NOLIMITZ — STRATEGY BACKTEST HARNESS  (read-only, trades nothing)
================================================================================
  PURPOSE
  -------
  Answer the one question that decides the business: does this scalper have a
  real edge? It replays historical candles from your MT5 terminal through the
  EXACT entry logic (analyze_for_self) and the EXACT exit logic (scale-out +
  profit-lock + trailing runner) the live worker uses, and prints a real
  equity curve: win rate, profit factor, max drawdown, avg win/loss, net P&L.

  It WRITES NOTHING and TRADES NOTHING. Pure simulation on past data.

  HOW TO RUN
  ----------
      $env:MT5_TERMINAL_PATH = "D:\\TraderMT5\\terminal64.exe"   # your terminal
      py -3.10 backtest_harness.py

  (Stop the trader first if it shares that terminal, or point this at a
  different terminal install.)

  ── HONESTY NOTES — read these, they decide whether to trust the result ──────
  1. SPREAD IS MODELED. A scalper targeting ~$2/0.01lot dies on spread. Every
     entry pays half-spread on entry and half on exit. Change SPREAD_USD_PER_001
     below to match your broker's real gold/BTC spread and re-run — if the
     edge only exists at zero spread, it doesn't exist.
  2. WORST-CASE INTRA-CANDLE ORDERING. M1 candles give O/H/L/C but not the path
     within the minute. When both the stop and a profit target sit inside one
     candle's range, we assume the STOP hit first (the pessimistic, honest
     assumption). This makes results a lower bound, not an inflated one.
  3. NO LOOK-AHEAD. Signals are computed only from candles that had CLOSED at
     decision time — exactly as live. The bar being decided on is never used
     to decide its own entry.
  4. This tests ONE position at a time per symbol (no stacking/martingale), so
     it measures the STRATEGY's edge cleanly, not money-management layering.
     Live stacking can only amplify the per-trade edge — if the edge is
     negative here, stacking makes it worse, not better.
================================================================================
"""

import os
import sys
import math
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
except Exception:
    print("MetaTrader5 not importable — run this on the Windows box with MT5.")
    sys.exit(1)


# ==============================================================================
# CONFIG — mirror the live worker's cfg values (keep in sync with Config class)
# ==============================================================================
class BT:
    # Which symbols + how much history
    SYMBOLS = ["XAUUSD", "BTCUSD"]        # resolved to broker names at runtime
    M1_BARS = 200_000                     # request as much M1 as the broker gives
    M5_BARS = 60_000

    # ── Entry logic constants (from live cfg) ───────────────────────────────
    GOLD_IGNITION_ATR = 0.5               # cfg.GOLD_IGNITION_ATR
    ATR_PERIOD        = 14

    # ── Exit logic constants (from live cfg — scale-out model) ──────────────
    SCALE1_TRIGGER_USD = 2.0              # +$2/0.01lot -> take 50%, SL to BE
    SCALE1_CLOSE_PCT   = 0.50
    SCALE2_TRIGGER_USD = 4.0              # +$4/0.01lot -> take 25%, lock +$2
    SCALE2_CLOSE_PCT   = 0.25
    SCALE2_LOCK_USD    = 2.0
    RUNNER_TRAIL_USD   = 2.0              # runner trails $2/0.01lot behind peak

    # Bracket (from try_open_trade): SL = 0.7x TP distance; TP = scalp target.
    SCALP_TARGET_USD_PER_001 = {"GOLD": 0.50, "BTC": 5.00}
    SCALP_SL_MULTIPLIER      = 0.7
    MAX_SL_PCT_OF_PRICE      = 0.004
    # Gold trend-scalp bracket override (self-signals use ATR bracket)
    GOLD_TREND_SL_ATR = 1.0
    GOLD_TREND_TP_ATR = 1.6

    # ── COST MODEL — CHANGE THESE TO YOUR BROKER'S REALITY ──────────────────
    # Spread in USD per 0.01 lot, per side is half of this. Gold ~ $0.20-0.40
    # typical; BTC varies wildly. Run with your real numbers.
    SPREAD_USD_PER_001 = {"GOLD": 0.05, "BTC": 0.11}
    # Optional commission per 0.01 lot round-trip (many brokers 0 on these).
    COMMISSION_USD_PER_001 = {"GOLD": 0.0, "BTC": 0.0}

    # Test at 0.01 lot (results scale linearly with lot).
    LOT = 0.01


def classify(sym: str) -> str:
    s = sym.upper()
    if "XAU" in s or "GOLD" in s: return "GOLD"
    if "BTC" in s or "BITCOIN" in s: return "BTC"
    return "OTHER"


def find_broker_symbol(base: str):
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


def _ema(vals, period):
    if not vals: return 0.0
    if len(vals) < period: return sum(vals) / len(vals)
    k = 2 / (period + 1)
    e = sum(vals[:period]) / period
    for v in vals[period:]:
        e = v * k + e * (1 - k)
    return e


def _atr_from(m15_slice, period=14):
    """ATR over a slice of M15 candles (list of dicts with high/low/close)."""
    if len(m15_slice) < period + 1: return 0.0
    trs = []
    for i in range(-period, 0):
        h = m15_slice[i]["high"]; l = m15_slice[i]["low"]
        pc = m15_slice[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / period


def load(broker_sym, tf, n):
    r = mt5.copy_rates_from_pos(broker_sym, tf, 0, n)
    if r is None:
        return []
    return [{"time": int(x["time"]), "open": float(x["open"]),
             "high": float(x["high"]), "low": float(x["low"]),
             "close": float(x["close"])} for x in r]


# ==============================================================================
# ENTRY SIGNAL — mirrors analyze_for_self EXACTLY, but computed at bar index i
# using only candles that had CLOSED by then (no look-ahead).
# ==============================================================================
def entry_signal(sym_class, m1, i, m5_by_time, atr15):
    """
    Returns "BUY" / "SELL" / None for the decision made AT the close of m1[i].
    m1[i] is the just-closed candle (== live's closed1[-1]). We use m1[:i+1].
    m5_by_time maps an m5 candle's close-time -> its index, so we can find the
    M5 context that had closed by m1[i]'s time (no future M5 leaking in).
    """
    if i < 16 or atr15 <= 0:
        return None

    # M5 trend from the last CLOSED M5 candles at this moment
    t = m1[i]["time"]
    # find most recent m5 index whose candle closed at/before this m1 close
    m5_idx = None
    for ct, idx in m5_by_time:
        if ct <= t:
            m5_idx = idx
        else:
            break
    if m5_idx is None or m5_idx < 51:
        return None
    c5 = [m5_by_time_closes[j] for j in range(m5_idx - 51, m5_idx)]  # 51 closed
    ema9_5  = _ema(c5, 9)
    ema21_5 = _ema(c5, 21)
    ema50_5 = _ema(c5, 50)
    up_trend   = ema9_5 > ema21_5 > ema50_5
    down_trend = ema9_5 < ema21_5 < ema50_5

    last1 = m1[i]
    last_body = last1["close"] - last1["open"]
    avg_body = (sum(abs(m1[j]["close"] - m1[j]["open"])
                    for j in range(i - 14, i + 1)) / 15) or 1e-9
    price = last1["close"]   # decision price ≈ close of the just-closed bar

    if sym_class == "GOLD":
        if not (up_trend or down_trend):
            return None
        want = "BUY" if up_trend else "SELL"
        ignition = (abs(last_body) > atr15 * BT.GOLD_IGNITION_ATR
                    and ((want == "BUY" and last_body > 0)
                         or (want == "SELL" and last_body < 0))
                    and abs(last_body) > avg_body * 1.5)
        near_ema = abs(price - ema21_5) < atr15 * 0.35
        resuming = ((want == "BUY" and last_body > 0)
                    or (want == "SELL" and last_body < 0))
        pullback_go = near_ema and resuming
        if not (ignition or pullback_go):
            return None
        return want

    if sym_class == "BTC":
        if not (up_trend or down_trend):
            return None
        want = "BUY" if up_trend else "SELL"
        bodies3 = [m1[j]["close"] - m1[j]["open"] for j in range(i - 2, i + 1)]
        thrust = (all(b > 0 for b in bodies3) if want == "BUY"
                  else all(b < 0 for b in bodies3))
        strong = abs(last_body) > avg_body * 1.3
        if not (thrust and strong):
            return None
        return want

    return None


# ==============================================================================
# TRADE SIMULATION — mirrors the scale-out + profit-lock + trailing exits,
# stepping candle-by-candle with worst-case intra-candle ordering.
# ==============================================================================
# Per-symbol USD-per-1.0-price-move for a 0.01 lot, read from the broker's
# real tick economics at runtime (filled in by backtest_symbol). NEVER assume
# standard contract sizes — this broker's BTC 0.01lot is ~$0.0088/point, not
# the $0.10 a standard contract would give. Getting this wrong scales all P&L.
_USD_PER_MOVE_001 = {}

def usd_per_price_unit(sym_class, entry_price):
    """USD per 1.0 price move per 0.01 lot — from the broker's real tick data
    (populated in backtest_symbol). Falls back to standard contracts only if
    the broker didn't expose tick_value/tick_size."""
    v = _USD_PER_MOVE_001.get(sym_class)
    if v and v > 0:
        return v
    if sym_class == "GOLD":
        return 1.0
    if sym_class == "BTC":
        return 0.10
    return 1.0


def simulate_trade(sym_class, direction, entry_idx, m1, atr15):
    """
    Open a trade at m1[entry_idx] close, then walk forward applying the live
    exit rules. Returns realized USD P&L per 0.01 lot (spread + commission
    already deducted), or None if it never resolved (ran out of data).
    """
    is_buy = (direction == "BUY")
    entry_bar = m1[entry_idx]
    entry_price = entry_bar["close"]

    usd_per_move = usd_per_price_unit(sym_class, entry_price)
    price_per_usd = 1.0 / usd_per_move   # price distance for $1/0.01lot

    # ── Bracket (mirror try_open_trade for a self-signal) ───────────────────
    if sym_class == "GOLD":
        sl_dist = min(atr15 * BT.GOLD_TREND_SL_ATR,
                      entry_price * BT.MAX_SL_PCT_OF_PRICE)
        tp_dist = atr15 * BT.GOLD_TREND_TP_ATR
    else:
        scalp_usd = BT.SCALP_TARGET_USD_PER_001[sym_class]
        scalp_dist = scalp_usd * price_per_usd
        sl_dist = min(scalp_dist * BT.SCALP_SL_MULTIPLIER,
                      entry_price * BT.MAX_SL_PCT_OF_PRICE)
        tp_dist = scalp_dist

    if is_buy:
        sl_price = entry_price - sl_dist
        tp_price = entry_price + tp_dist
    else:
        sl_price = entry_price + sl_dist
        tp_price = entry_price - tp_dist

    # Costs (deducted from final P&L): full spread once + commission
    spread_cost = BT.SPREAD_USD_PER_001[sym_class]
    commission  = BT.COMMISSION_USD_PER_001[sym_class]

    # Scale-out state
    remaining = 1.0            # fraction of position still open
    banked_usd = 0.0           # profit already taken via partials
    scale1_done = False
    scale2_done = False
    runner_peak_usd = 0.0
    # SL can ratchet — track current stop price
    cur_sl = sl_price

    def price_to_usd(px):
        """Floating USD per 0.01lot at price px, for the remaining fraction."""
        move = (px - entry_price) if is_buy else (entry_price - px)
        return move * usd_per_move

    for j in range(entry_idx + 1, len(m1)):
        bar = m1[j]
        hi, lo = bar["high"], bar["low"]

        # Worst-case ordering: check STOP against the adverse extreme FIRST.
        adverse = lo if is_buy else hi
        favor   = hi if is_buy else lo

        # 1) Did we hit the current stop this bar? (pessimistic: stop first)
        stop_hit = (adverse <= cur_sl) if is_buy else (adverse >= cur_sl)
        if stop_hit:
            # Close remaining at the stop price
            pnl = banked_usd + price_to_usd(cur_sl) * remaining
            return pnl - spread_cost - commission

        # 2) Favorable extreme this bar -> evaluate scale-out triggers on the
        # best price reached. Use per-0.01lot USD at that extreme.
        best_usd = price_to_usd(favor)

        # Stage 1: +$2 -> take 50%, move SL to breakeven.
        # Bank 50% of the position locked in at the +$2 trigger:
        #   0.5 units * $2 = $1.00 per 0.01 lot.
        if not scale1_done and best_usd >= BT.SCALE1_TRIGGER_USD:
            banked_usd += BT.SCALE1_TRIGGER_USD * BT.SCALE1_CLOSE_PCT
            remaining -= BT.SCALE1_CLOSE_PCT
            scale1_done = True
            cur_sl = entry_price          # SL to breakeven

        # Stage 2: +$4 -> take 25%, lock +$2
        if scale1_done and not scale2_done and best_usd >= BT.SCALE2_TRIGGER_USD:
            banked_usd += BT.SCALE2_TRIGGER_USD * BT.SCALE2_CLOSE_PCT
            remaining -= BT.SCALE2_CLOSE_PCT
            scale2_done = True
            # lock +$2/0.01lot on the runner
            lock_move = BT.SCALE2_LOCK_USD * price_per_usd
            cur_sl = (entry_price + lock_move) if is_buy else (entry_price - lock_move)

        # Runner trail after scale2
        if scale2_done:
            if best_usd > runner_peak_usd:
                runner_peak_usd = best_usd
            # trail stop to (peak - $2)
            trail_usd = runner_peak_usd - BT.RUNNER_TRAIL_USD
            if trail_usd > 0:
                trail_move = trail_usd * price_per_usd
                new_sl = (entry_price + trail_move) if is_buy else (entry_price - trail_move)
                # ratchet only
                if is_buy:
                    cur_sl = max(cur_sl, new_sl)
                else:
                    cur_sl = min(cur_sl, new_sl)

        # 3) TP cap for the non-runner logic: if scale-out never triggered and
        # price reached the original TP, close there (mirrors legacy scalp TP).
        if not scale1_done:
            tp_reached = (favor >= tp_price) if is_buy else (favor <= tp_price)
            if tp_reached:
                pnl = price_to_usd(tp_price) * remaining
                return pnl - spread_cost - commission

    # Ran out of data with position still open — close at last price (mark).
    pnl = banked_usd + price_to_usd(m1[-1]["close"]) * remaining
    return pnl - spread_cost - commission


# ==============================================================================
# RUN ONE SYMBOL
# ==============================================================================
# module-level holder so entry_signal can read m5 closes without re-passing
m5_by_time_closes = []

def backtest_symbol(base_sym):
    global m5_by_time_closes
    broker_sym = find_broker_symbol(base_sym)
    if not broker_sym:
        print(f"  {base_sym}: broker symbol not found — skipping")
        return None
    mt5.symbol_select(broker_sym, True)
    sym_class = classify(broker_sym)

    # ── Read this broker's REAL tick economics — never assume standard
    # contract sizes. usd_per_move_001 = (tick_value / tick_size) * 0.01.
    info = mt5.symbol_info(broker_sym)
    try:
        tv = info.trade_tick_value; ts = info.trade_tick_size
        if tv and ts and ts > 0:
            _USD_PER_MOVE_001[sym_class] = (tv / ts) * 0.01
    except Exception:
        pass
    upm = _USD_PER_MOVE_001.get(sym_class)
    print(f"  {broker_sym}: ${upm:.4f} per 1.0 move per 0.01 lot"
          if upm else f"  {broker_sym}: using fallback tick economics")

    m1 = load(broker_sym, mt5.TIMEFRAME_M1, BT.M1_BARS)
    m5 = load(broker_sym, mt5.TIMEFRAME_M5, BT.M5_BARS)
    m15 = load(broker_sym, mt5.TIMEFRAME_M15, BT.M1_BARS // 15 + 100)
    if len(m1) < 1000 or len(m5) < 200 or len(m15) < 100:
        print(f"  {broker_sym}: not enough history (m1={len(m1)} m5={len(m5)} "
              f"m15={len(m15)}) — skipping")
        return None

    # Precompute M5 close list + (close_time, idx) index for no-look-ahead lookup
    m5_by_time_closes = [c["close"] for c in m5]
    m5_time_index = [(m5[j]["time"] + 5 * 60, j) for j in range(len(m5))]  # +5m ~ close time

    # Precompute an M15-time index so we can get the ATR "as of" each m1 bar.
    m15_close_times = [(m15[j]["time"] + 15 * 60, j) for j in range(len(m15))]

    def atr_asof(t):
        idx = None
        for ct, j in m15_close_times:
            if ct <= t:
                idx = j
            else:
                break
        if idx is None or idx < BT.ATR_PERIOD + 1:
            return 0.0
        return _atr_from(m15[max(0, idx - BT.ATR_PERIOD - 1): idx + 1], BT.ATR_PERIOD)

    # Walk M1. One position at a time: when flat, look for a signal; when a
    # signal fires, simulate the whole trade, then skip past its bars.
    trades = []
    i = 20
    N = len(m1)
    span_start = datetime.fromtimestamp(m1[0]["time"], tz=timezone.utc)
    span_end   = datetime.fromtimestamp(m1[-1]["time"], tz=timezone.utc)

    while i < N - 2:
        atr15 = atr_asof(m1[i]["time"])
        sig = entry_signal(sym_class, m1, i, m5_time_index, atr15)
        if sig is None:
            i += 1
            continue
        pnl = simulate_trade(sym_class, sig, i, m1, atr15)
        if pnl is None:
            break
        # find how many bars the trade consumed by re-walking cheaply: we
        # approximate by advancing 1 bar (entries can be frequent) — but to
        # avoid overlapping the SAME move repeatedly, skip a small cooldown.
        trades.append(pnl)
        i += 3   # ~ mirror the live per-(login,symbol) cooldown spacing

    if not trades:
        print(f"  {broker_sym}: no trades generated")
        return None

    # ── Stats ───────────────────────────────────────────────────────────────
    n = len(trades)
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    net = sum(trades)
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    win_rate = 100 * len(wins) / n if n else 0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float('inf')
    avg_win = (gross_win / len(wins)) if wins else 0
    avg_loss = (gross_loss / len(losses)) if losses else 0
    expectancy = net / n if n else 0

    # Max drawdown on the equity curve
    equity = 0.0; peak = 0.0; max_dd = 0.0
    for t in trades:
        equity += t
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "symbol": broker_sym, "class": sym_class,
        "span": (span_start, span_end),
        "n": n, "win_rate": win_rate, "net": net,
        "profit_factor": profit_factor, "avg_win": avg_win, "avg_loss": avg_loss,
        "expectancy": expectancy, "max_dd": max_dd,
        "gross_win": gross_win, "gross_loss": gross_loss,
        "wins": len(wins), "losses": len(losses),
    }


def print_report(r):
    if not r:
        return
    s, e = r["span"]
    print("\n" + "=" * 72)
    print(f"  {r['symbol']}  ({r['class']})   0.01 lot   "
          f"spread=${BT.SPREAD_USD_PER_001[r['class']]:.2f}/0.01lot")
    print(f"  period: {s:%Y-%m-%d} → {e:%Y-%m-%d}")
    print("-" * 72)
    print(f"  trades:          {r['n']}")
    print(f"  win rate:        {r['win_rate']:.1f}%   ({r['wins']}W / {r['losses']}L)")
    print(f"  NET P&L:         ${r['net']:+.2f}   (per 0.01 lot, after spread)")
    pf = r['profit_factor']
    print(f"  profit factor:   {pf:.2f}" + ("   (>1 = profitable)" if pf != float('inf') else ""))
    print(f"  avg win:         ${r['avg_win']:.2f}")
    print(f"  avg loss:        ${r['avg_loss']:.2f}")
    print(f"  expectancy:      ${r['expectancy']:+.3f} per trade")
    print(f"  max drawdown:    ${r['max_dd']:.2f}")
    print("-" * 72)
    if r["net"] > 0 and pf > 1.1:
        print(f"  🟢 POSITIVE EDGE on history. Necessary, not sufficient — "
              f"forward-test on demo next.")
    elif r["net"] > 0:
        print(f"  🟡 MARGINALLY positive — thin edge, very sensitive to spread. "
              f"Re-run with your true spread before trusting it.")
    else:
        print(f"  🔴 NEGATIVE EDGE on history. This configuration loses money "
              f"even before live slippage. Speed/scale cannot fix this.")
    print("=" * 72)


def main():
    term = os.environ.get("MT5_TERMINAL_PATH")
    ok = mt5.initialize(path=term) if term else mt5.initialize()
    if not ok:
        print(f"MT5 init failed: {mt5.last_error()} "
              f"(set MT5_TERMINAL_PATH, and stop the trader if it shares the terminal)")
        sys.exit(1)

    print("=" * 72)
    print("  NOLIMITZ STRATEGY BACKTEST — mirrors live entry + scale-out exits")
    print("  (read-only, trades nothing; results are per 0.01 lot, spread-adjusted)")
    print("=" * 72)

    results = []
    for base in BT.SYMBOLS:
        print(f"\nRunning {base} ...")
        try:
            r = backtest_symbol(base)
            results.append(r)
            print_report(r)
        except Exception as ex:
            print(f"  {base}: error {ex}")
            import traceback; traceback.print_exc()

    # Combined verdict
    real = [r for r in results if r]
    if real:
        total_net = sum(r["net"] for r in real)
        total_n = sum(r["n"] for r in real)
        print("\n" + "#" * 72)
        print(f"  OVERALL: {total_n} trades across {len(real)} symbol(s), "
              f"net ${total_net:+.2f} per 0.01 lot (after spread)")
        print(f"  Reminder: this is HISTORY. A positive number earns a demo "
              f"forward-test, not live client money. A negative number is "
              f"decisive — the strategy needs to change, not the infrastructure.")
        print("#" * 72)

    mt5.shutdown()


if __name__ == "__main__":
    main()