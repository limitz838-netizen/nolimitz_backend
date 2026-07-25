"""
================================================================================
  NOLIMITZ — PRE-REGISTERED FILTER TEST: GOLD PRIOR-DAY SWEEP REJECTION
================================================================================
  The base strategy is the only idea so far that's positive on BOTH train and
  test (5 years, 1,097 trades) — but at out-of-sample PF 1.02 it's a silhouette
  of an edge, not an edge. This script runs ONE disciplined refinement round.

  PRE-REGISTERED — meaning these exact variants were stated BEFORE testing,
  each with a causal mechanism, and NOTHING else will be tried afterwards on
  this dataset (that would be curve-fitting with extra steps):

    F0  BASELINE          the strategy as already tested (comparison anchor)
    F1  SESSION FILTER    entries only 07:00–16:59 UTC. Mechanism: a stop-hunt
                          needs real participants to reverse against; Asian-
                          session pokes of yesterday's level are mostly drift.
    F2  DEPTH FILTER      wick beyond the prior-day level must be ≥ 0.3 ATR.
                          Mechanism: a one-tick poke isn't a hunt; a deep wick
                          slammed back is.
    F3  BOTH              F1 + F2 together.

  DECISION RULE (stated in advance):
    • A variant is actionable ONLY if positive on train AND test, with
      out-of-sample PF ≥ 1.15 and ≥ 150 test trades.
    • If no variant qualifies → the gold sweep is SHELVED. No further filters
      will be fished for on this data.

  Same honesty mechanics as before: spread deducted, stop-first fills, target
  multiple chosen on TRAIN only (tiny grid), judged on TEST (never touched).

  RUN (needs backtest_harness.py + htf_strategies.py in the same folder):
      $env:MT5_TERMINAL_PATH = "D:\\TraderMT5\\terminal64.exe"
      py -3.10 filter_test.py
================================================================================
"""

import os, sys

try:
    import MetaTrader5 as mt5
except Exception:
    print("MetaTrader5 not importable — run on the Windows box.")
    sys.exit(1)

import backtest_harness as H
import htf_strategies as HT


SESSION_LO, SESSION_HI = 7, 17          # F1: entries 07:00–16:59 UTC
MIN_DEPTH_ATR = 0.3                     # F2: wick beyond level ≥ 0.3 ATR
MIN_TEST_TRADES = 150                   # decision rule
MIN_TEST_PF = 1.15


def entries_sweep_with_meta(m15, atr, day_hi, day_lo, prev_of):
    """Same detection as htf_strategies.entries_sweep_rejection, but each spec
    also records hour and sweep depth (in ATR units) so filters can apply.
    Detection logic itself is UNCHANGED — filters only subtract entries."""
    out = []
    used = set()
    for i, b in enumerate(m15):
        pd = prev_of.get(b["day"])
        if pd is None:
            continue
        a = atr[i]
        if a <= 0:
            continue
        ph, pl = day_hi[pd], day_lo[pd]
        if (b["day"], "HI") not in used and b["high"] > ph and b["close"] < ph:
            out.append({"idx": i, "dir": "SELL",
                        "stop": b["high"] + 0.2 * a, "atr": a, "session": False,
                        "hour": b["hour"], "depth_atr": (b["high"] - ph) / a})
            used.add((b["day"], "HI"))
        if (b["day"], "LO") not in used and b["low"] < pl and b["close"] > pl:
            out.append({"idx": i, "dir": "BUY",
                        "stop": b["low"] - 0.2 * a, "atr": a, "session": False,
                        "hour": b["hour"], "depth_atr": (pl - b["low"]) / a})
            used.add((b["day"], "LO"))
    return out


VARIANTS = [
    ("F0 BASELINE (as already tested)",
     lambda s: True),
    ("F1 SESSION 07-17 UTC only",
     lambda s: SESSION_LO <= s["hour"] < SESSION_HI),
    ("F2 DEPTH >= 0.3 ATR",
     lambda s: s["depth_atr"] >= MIN_DEPTH_ATR),
    ("F3 SESSION + DEPTH",
     lambda s: (SESSION_LO <= s["hour"] < SESSION_HI)
               and s["depth_atr"] >= MIN_DEPTH_ATR),
]


def run_variant(label, keep, sym_class, m15, specs, split=0.6):
    kept = [s for s in specs if keep(s)]
    split_idx = int(len(m15) * split)
    train = [s for s in kept if s["idx"] < split_idx]
    test  = [s for s in kept if s["idx"] >= split_idx]

    print(f"\n  ── {label} ──")
    print(f"    entries kept: {len(kept)} of {len(specs)}  "
          f"({len(train)} train / {len(test)} test)")

    rows = []
    for R in (1.5, 2.0, 3.0):
        r = HT.stats([p for p in (HT.simulate(sym_class, m15, s, R)
                                  for s in train) if p is not None])
        if r:
            rows.append((R, r))
    if not rows:
        print("    ⚠️  too few TRAIN trades — variant unjudgeable")
        return None
    rows.sort(key=lambda x: -x[1]["net"])
    for R, r in rows:
        print(f"    train R={R:<4} {HT.fmt(r)}")
    best_R, best_train = rows[0]

    test_r = HT.stats([p for p in (HT.simulate(sym_class, m15, s, best_R)
                                   for s in test) if p is not None])
    print(f"    ── out-of-sample (R={best_R}) ──")
    print(f"    TEST      {HT.fmt(test_r)}")

    if not test_r:
        print("    ⚠️  too few TEST trades to judge")
        return None
    qualifies = (best_train["net"] > 0 and test_r["net"] > 0
                 and test_r["pf"] >= MIN_TEST_PF
                 and test_r["n"] >= MIN_TEST_TRADES)
    if qualifies:
        print(f"    🟢 QUALIFIES under the pre-registered rule "
              f"(test PF {test_r['pf']:.2f} ≥ {MIN_TEST_PF}, "
              f"n {test_r['n']} ≥ {MIN_TEST_TRADES}, both periods positive).")
    elif best_train["net"] > 0 and test_r["net"] > 0:
        print(f"    🟡 Positive both periods but below the bar "
              f"(need test PF ≥ {MIN_TEST_PF} and n ≥ {MIN_TEST_TRADES}).")
    else:
        print(f"    🔴 Does not survive.")
    return {"label": label, "best_R": best_R, "train": best_train,
            "test": test_r, "qualifies": qualifies}


def main():
    term = os.environ.get("MT5_TERMINAL_PATH")
    ok = mt5.initialize(path=term) if term else mt5.initialize()
    if not ok:
        print(f"MT5 init failed: {mt5.last_error()}"); sys.exit(1)

    print("=" * 78)
    print("  PRE-REGISTERED FILTER TEST — gold prior-day sweep rejection")
    print("  Four variants stated in advance. Decision rule: test PF ≥ 1.15,")
    print("  ≥ 150 test trades, positive in BOTH periods — or the idea is shelved.")
    print("=" * 78)

    broker_sym = H.find_broker_symbol("XAUUSD")
    if not broker_sym:
        print("gold symbol not found"); sys.exit(1)
    mt5.symbol_select(broker_sym, True)
    info = mt5.symbol_info(broker_sym)
    try:
        tv, ts = info.trade_tick_value, info.trade_tick_size
        if tv and ts and ts > 0:
            H._USD_PER_MOVE_001["GOLD"] = (tv / ts) * 0.01
    except Exception:
        pass

    prepped = HT.prep(broker_sym)
    if not prepped:
        print("not enough M15 history"); sys.exit(1)
    m15, atr, day_hi, day_lo, prev_of = prepped
    print(f"\n  {broker_sym}: {m15[0]['dt']:%Y-%m-%d} → {m15[-1]['dt']:%Y-%m-%d} "
          f"({len(m15)} M15 bars)  spread=${H.BT.SPREAD_USD_PER_001['GOLD']:.2f}")

    specs = entries_sweep_with_meta(m15, atr, day_hi, day_lo, prev_of)

    results = []
    for label, keep in VARIANTS:
        r = run_variant(label, keep, "GOLD", m15, specs)
        if r:
            results.append(r)

    print("\n" + "#" * 78)
    winners = [r for r in results if r["qualifies"]]
    if winners:
        best = max(winners, key=lambda r: r["test"]["pf"])
        print(f"  🏆 QUALIFIED: {best['label']}  (R={best['best_R']})")
        print(f"     TEST: {HT.fmt(best['test'])}")
        print(f"     NEXT STEP: demo forward-test THIS exact configuration for")
        print(f"     2-4 weeks. Compare demo results to these numbers before any")
        print(f"     client money. This is the first configuration in the whole")
        print(f"     project to earn that step.")
    else:
        print(f"  ❌ NO VARIANT QUALIFIED. Per the pre-registered rule, the gold")
        print(f"     sweep-rejection idea is now SHELVED — no further filters will")
        print(f"     be fished for on this dataset. Next: different entry ideas")
        print(f"     (NY session, volatility regimes, H1 structure) through the")
        print(f"     same pipeline — or shift focus to monetizing the platform")
        print(f"     without being the signal source.")
    print("#" * 78)
    mt5.shutdown()


if __name__ == "__main__":
    main()