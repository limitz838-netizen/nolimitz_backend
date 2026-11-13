"""
Final validation of the strategy picked by strategy_shootout.py (on 2000-2015 data only).

  1. Unseen period 2016-now vs buy & hold
  2. Coin-flip Monte Carlo: same number of trades, same holding times, same exposure —
     only WHEN you are in and WHICH direction are random. Two flavours:
       a) 50/50 coin for long vs short      b) random timing, long only (beats gold's drift?)
  3. Walk-forward: every 2 years re-run the WHOLE selection (family + params) on the past only,
     then trade the next 2 years blind. Stitch those blind years together.
  4. A timeframe it never saw: 1-hour bars 2024-2026, same parameters.
"""
import json
import numpy as np
import pandas as pd
from engine import load, simulate, stats, score, window_trades, neighbourhood_pick, FAMILIES, COST

TRAIN_END = pd.Timestamp("2015-12-31")
RUNS = 5000


def pick(df, end):
    best = None
    for name, (fn, grid) in FAMILIES.items():
        t = neighbourhood_pick(df, fn, grid, df.index[0], end)
        b = t.iloc[0]
        p = {k: (bool(b[k]) if k == "shorts" else type(grid[k][0])(b[k])) for k in grid}
        if best is None or b.robust > best[3]:
            best = (name, fn, p, b.robust)
    return best


def runs(v):
    """Split a position vector into (value, length) runs."""
    out = []; i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[j + 1] == v[i]:
            j += 1
        out.append((v[i], j - i + 1)); i = j + 1
    return out


def cc_returns(pos, c):
    """Close-to-close P&L of a position vector, costs on every change. Used for BOTH the strategy and
    the coin flips, so the comparison is apples to apples."""
    r = np.r_[0, c[1:] / c[:-1] - 1]
    chg = np.abs(np.diff(np.r_[0, pos]))
    return pos * r - COST * chg


def coin_flip(held, c, long_only, rng):
    rs = runs(held)
    trades = [n for v, n in rs if v != 0]
    gaps = [n for v, n in rs if v == 0]
    rng.shuffle(trades); rng.shuffle(gaps)
    seq, ti, gi = [], 0, 0
    start_with_gap = rng.random() < len(gaps) / max(len(gaps) + len(trades), 1)
    turn_gap = start_with_gap
    while ti < len(trades) or gi < len(gaps):
        if (turn_gap and gi < len(gaps)) or ti >= len(trades):
            seq += [0] * gaps[gi]; gi += 1
        else:
            d = 1 if long_only or rng.random() < 0.5 else -1
            seq += [d] * trades[ti]; ti += 1
            if gi < len(gaps):           # keep trades separated by a gap
                seq += [0] * gaps[gi]; gi += 1
        turn_gap = False
    return np.array(seq[:len(held)])


def fmt(s):
    return (f"return {s['total']*100:7.1f}% | CAGR {s['cagr']*100:5.1f}% | Sharpe {s['sharpe']:5.2f} | "
            f"maxDD {s['maxdd']*100:6.1f}% | trades {s['trades']:3d} | win {s['winrate']*100:4.1f}% | "
            f"PF {s['pf']:5.2f} | in-market {s['exposure']*100:3.0f}%")


def main():
    D = load("data/gold_daily.csv")
    last = D.index[-1]
    test0 = TRAIN_END + pd.Timedelta(days=1)
    R = {}

    name, fn, P, rob = pick(D, TRAIN_END)
    print(f"Strategy chosen on 2000-2015 ONLY: {name} {P}\n")
    R["strategy"] = dict(name=name, params=P)

    # ---- 1) seen vs unseen
    tr, _ = score(D, fn, P, D.index[0], TRAIN_END)
    te, te_r = score(D, fn, P, test0, last)
    m = D.index >= test0
    bh = D.Close[m].pct_change().fillna(0)
    bh_s = stats(bh, np.array([bh.add(1).prod() - 1]), 252)
    print("TRAIN 2000-2015 (seen)   :", fmt(tr))
    print("TEST  2016-now  (UNSEEN) :", fmt(te))
    print(f"Buy & hold 2016-now      : return {bh_s['total']*100:7.1f}% | CAGR {bh_s['cagr']*100:5.1f}% | "
          f"Sharpe {bh_s['sharpe']:5.2f} | maxDD {bh_s['maxdd']*100:6.1f}%")
    R.update(train=tr, test=te, buyhold_test=bh_s)

    # ---- 2) coin flip on the unseen period
    el, es, xl, xs, K = fn(D, **P)
    _, _, held = simulate(D, el, es, xl, xs, K)
    h_te, c_te = held[m], D.Close.to_numpy()[m]
    strat_cc = pd.Series(cc_returns(h_te, c_te), index=D.index[m])
    s_cc = stats(strat_cc, np.array([0.0]), 252)
    print(f"\nCOIN-FLIP Monte Carlo on UNSEEN 2016-now ({RUNS} runs; same # trades, same hold lengths, same exposure):")
    print(f"  strategy (close-to-close basis used for all runs): return {s_cc['total']*100:.1f}%, Sharpe {s_cc['sharpe']:.2f}")
    rng = np.random.default_rng(7)
    for label, lo in (("coin flip long/short", False), ("random timing, long only", True)):
        sims = []
        for _ in range(RUNS):
            pos = coin_flip(h_te, c_te, lo, rng)
            s = stats(pd.Series(cc_returns(pos, c_te)), np.array([0.0]), 252)
            sims.append((s["total"], s["sharpe"], s["maxdd"]))
        sims = np.array(sims)
        p = (sims[:, 1] >= s_cc["sharpe"]).mean()
        beat = (sims[:, 0] < s_cc["total"]).mean()
        print(f"  {label:25s}: median return {np.median(sims[:,0])*100:6.1f}% "
              f"[5%..95%: {np.percentile(sims[:,0],5)*100:.0f}%..{np.percentile(sims[:,0],95)*100:.0f}%] "
              f"median Sharpe {np.median(sims[:,1]):.2f} | median maxDD {np.median(sims[:,2])*100:.0f}%")
        print(f"  {'':25s}  strategy beats {beat*100:.1f}% of runs on return; p-value (Sharpe) = {p:.4f}")
        R[f"coin_{'long' if lo else 'both'}"] = dict(
            median_ret=float(np.median(sims[:, 0])), p5=float(np.percentile(sims[:, 0], 5)),
            p95=float(np.percentile(sims[:, 0], 95)), median_sharpe=float(np.median(sims[:, 1])),
            median_maxdd=float(np.median(sims[:, 2])), beat=float(beat), p_value=float(p))
        np.save(f"data/coin_{'long' if lo else 'both'}.npy", sims)
    R["strategy_cc"] = s_cc

    # ---- 3) walk-forward with full re-selection
    print("\nWALK-FORWARD (re-pick family+params on the past only, trade next 2 years blind):")
    rows, pieces = [], []
    for y in range(2008, last.year + 1, 2):
        end = pd.Timestamp(f"{y-1}-12-31")
        w0, w1 = pd.Timestamp(f"{y}-01-01"), min(pd.Timestamp(f"{y+1}-12-31"), last)
        n_, f_, p_, _ = pick(D, end)
        s, r = score(D, f_, p_, w0, w1)
        b = D.Close[(D.index >= w0) & (D.index <= w1)]
        pieces.append(r)
        rows.append(dict(window=f"{y}-{w1.year}", family=n_, params=p_, ret=s["total"],
                         sharpe=s["sharpe"], maxdd=s["maxdd"], buyhold=b.iloc[-1] / b.iloc[0] - 1))
        print(f"  {y}-{w1.year}: {n_:30s} {str(p_):45s} -> {s['total']*100:6.1f}% "
              f"(maxDD {s['maxdd']*100:5.1f}%)  buy&hold {rows[-1]['buyhold']*100:6.1f}%")
    wf = pd.concat(pieces)
    wf_s = stats(wf, np.array([0.0]), 252)
    b = D.Close[D.index >= pd.Timestamp("2008-01-01")]
    bh_wf = stats(b.pct_change().fillna(0), np.array([0.0]), 252)
    print(f"  STITCHED blind 2008-now: return {wf_s['total']*100:.0f}% CAGR {wf_s['cagr']*100:.1f}% "
          f"Sharpe {wf_s['sharpe']:.2f} maxDD {wf_s['maxdd']*100:.1f}%  |  buy&hold: return {bh_wf['total']*100:.0f}% "
          f"CAGR {bh_wf['cagr']*100:.1f}% Sharpe {bh_wf['sharpe']:.2f} maxDD {bh_wf['maxdd']*100:.1f}%")
    R["walkforward"] = dict(rows=rows, stitched=wf_s, buyhold=bh_wf)

    # ---- 4) never-seen timeframe
    H = load("data/gold_1h.csv")
    hs, _ = score(H, fn, P, H.index[0] + pd.Timedelta(days=20), H.index[-1], 252 * 23)
    hb = H.Close[H.index >= H.index[0] + pd.Timedelta(days=20)]
    print(f"\n1-HOUR bars {H.index[0].date()} -> {H.index[-1].date()} (same params, never tuned on 1h):\n  {fmt(hs)}"
          f"\n  buy & hold same span: {(hb.iloc[-1]/hb.iloc[0]-1)*100:.1f}%")
    R["h1"] = hs; R["h1_buyhold"] = float(hb.iloc[-1] / hb.iloc[0] - 1)

    # equity curves (signal-based sim, open fills) for the chart
    r_all, _, _ = simulate(D, el, es, xl, xs, K)
    eq = pd.DataFrame({"strategy": (1 + r_all).cumprod(), "gold": D.Close / D.Close.iloc[0],
                       "position": held}, index=D.index)
    eq.to_csv("data/equity.csv")
    json.dump(R, open("data/report.json", "w"), default=float, indent=1)


if __name__ == "__main__":
    main()
