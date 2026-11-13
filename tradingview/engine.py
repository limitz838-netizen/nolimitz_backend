"""Shared engine: data loading, indicators, bar-by-bar simulator, strategy families, stats.
All signals are taken on a bar CLOSE and filled at the NEXT bar open (no look-ahead).
"""
import itertools, json, sys
import numpy as np
import pandas as pd

COST = 0.0003          # 0.03% per side (spread + commission), charged on entry and exit
ATR_LEN = 14


# ---------------------------------------------------------------- data
def load(path):
    df = pd.read_csv(path, header=[0, 1], index_col=0)
    df.columns = [c[0] for c in df.columns]
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    df = df[(df.Close > 0) & (df.High >= df.Low)]
    return df


def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def atr(h, l, c, n=ATR_LEN):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().to_numpy()   # RMA, like ta.atr


# ---------------------------------------------------------------- stats
def stats(ret, trades, bars_per_year):
    eq = (1 + ret).cumprod()
    yrs = len(ret) / bars_per_year
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else 0
    sd = ret.std()
    sharpe = ret.mean() / sd * np.sqrt(bars_per_year) if sd > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    wins = trades[trades > 0]; losses = trades[trades <= 0]
    pf = wins.sum() / -losses.sum() if len(losses) and losses.sum() < 0 else np.inf
    return dict(total=eq.iloc[-1] - 1, cagr=cagr, sharpe=sharpe, maxdd=dd,
                trades=len(trades), winrate=len(wins) / len(trades) if len(trades) else 0,
                pf=pf, avg_trade=trades.mean() if len(trades) else 0,
                exposure=float((ret != 0).mean()))


def rsi(c, n):
    d = np.diff(c, prepend=c[0])
    up = pd.Series(np.maximum(d, 0)).ewm(alpha=1 / n, adjust=False).mean()
    dn = pd.Series(np.maximum(-d, 0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50).to_numpy()


def sma(c, n):
    return pd.Series(c).rolling(n).mean().to_numpy()


def simulate(df, el, es, xl, xs, K=None, rand_p=None, rand_long=False, seed=0, warm=260):
    """el/es: entry long/short at close; xl/xs: exit long/short at close; K: chandelier ATR stop."""
    o, h, l, c = (df[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    a = atr(h, l, c)
    n = len(c)
    if rand_p is not None:
        g = np.random.default_rng(seed)
        roll, flip = g.random(n) < rand_p, g.random(n) < 0.5
        el = roll & (flip | rand_long)
        es = roll & ~(flip | rand_long)
    ret = np.zeros(n); trades = []; held = np.zeros(n, int)
    pos = pend = 0; xpend = False; ext = stop = epx = np.nan
    for i in range(1, n):
        r = 0.0
        if xpend and pos:
            r += pos * (o[i] / c[i - 1] - 1) - COST
            trades.append(pos * (o[i] / epx - 1) - 2 * COST); pos = 0; xpend = False
        if pend and not pos:
            pos, epx, pend = pend, o[i], 0
            r += pos * (c[i] / o[i] - 1) - COST
            ext = h[i] if pos > 0 else l[i]
            stop = ext - pos * (K or 0) * a[i]
        elif pos:
            r += pos * (c[i] / c[i - 1] - 1)
        ret[i] = r
        held[i] = pos
        if i < warm:
            continue
        if pos > 0:
            if K: ext = max(ext, h[i]); stop = max(stop, ext - K * a[i])
            if xl[i] or (K and c[i] < stop): xpend = True
        elif pos < 0:
            if K: ext = min(ext, l[i]); stop = min(stop, ext + K * a[i])
            if xs[i] or (K and c[i] > stop): xpend = True
        if not pos and i < n - 1:
            pend = 1 if el[i] else (-1 if es[i] else 0)
    if pos:
        trades.append(pos * (c[-1] / epx - 1) - COST)
    return pd.Series(ret, index=df.index), np.array(trades), held


# ---------------------------------------------------------------- families
def fam_breakout(df, N, L, K, shorts):
    c, h, l = df.Close.to_numpy(), df.High.to_numpy(), df.Low.to_numpy()
    e = ema(c, L)
    hh = pd.Series(h).rolling(N).max().shift(1).to_numpy()
    ll = pd.Series(l).rolling(N).min().shift(1).to_numpy()
    z = np.zeros(len(c), bool)
    return (c > e) & (c > hh), shorts & (c < e) & (c < ll), z, z, K


def fam_rsi2(df, L, lvl, xlen, shorts):
    c = df.Close.to_numpy(); r = rsi(c, 2); trend = sma(c, L); fast = sma(c, xlen)
    return ((c > trend) & (r < lvl), shorts & (c < trend) & (r > 100 - lvl),
            c > fast, c < fast, None)


def fam_emax(df, f, s, shorts):
    c = df.Close.to_numpy(); ef, es_ = ema(c, f), ema(c, s)
    return ef > es_, shorts & (ef < es_), ef < es_, ef > es_, None


def fam_tsmom(df, lb, shorts):
    c = df.Close.to_numpy(); m = c / np.r_[np.full(lb, np.nan), c[:-lb]] - 1
    return m > 0, shorts & (m < 0), m <= 0, m >= 0, None


FAMILIES = {
    "Donchian breakout + ATR trail": (fam_breakout, dict(N=[10, 20, 40, 55], L=[50, 100, 200], K=[2.0, 3.0, 4.0, 5.0], shorts=[False, True])),
    "RSI(2) pullback in uptrend":    (fam_rsi2, dict(L=[100, 150, 200], lvl=[5, 10, 15, 20], xlen=[3, 5, 10], shorts=[False, True])),
    "EMA crossover":                 (fam_emax, dict(f=[10, 20, 50], s=[50, 100, 200], shorts=[False, True])),
    "12-1 time-series momentum":     (fam_tsmom, dict(lb=[63, 126, 189, 252], shorts=[False, True])),
}


def window_trades(ret, held, m):
    """Per-trade returns for trades that START inside boolean window m (cost-inclusive).
    A trade = a run of non-zero held bars plus the following bar (where the exit fill lands)."""
    r = ret.to_numpy(); out = []; i = 0; n = len(held)
    while i < n:
        if held[i] != 0 and (i == 0 or held[i - 1] != held[i]):
            j = i
            while j + 1 < n and held[j + 1] == held[i]:
                j += 1
            if m[i]:
                out.append(np.prod(1 + r[i:min(j + 2, n)]) - 1)
            i = j + 1
        else:
            i += 1
    return np.array(out)


def score(df, fn, p, s, e, bpy=252):
    el, es, xl, xs, K = fn(df, **p)
    r, _, held = simulate(df, el, es, xl, xs, K)
    m = (r.index >= s) & (r.index <= e)
    return stats(r[m], window_trades(r, held, m), bpy), r[m]


def neighbourhood_pick(df, fn, grid, s, e):
    keys = list(grid)
    rows = []
    for combo in itertools.product(*grid.values()):
        p = dict(zip(keys, combo))
        if fn is fam_emax and p["f"] >= p["s"]:
            continue
        st, _ = score(df, fn, p, s, e)
        rows.append({**p, **st})
    t = pd.DataFrame(rows)
    num = [k for k in keys if k != "shorts"]
    def nb(r):
        m = t.shorts == r.shorts
        for k in num:
            ix = grid[k].index
            m &= t[k].apply(lambda v: abs(ix(v) - ix(r[k])) <= 1)
        return t[m].sharpe.mean()
    t["robust"] = t.apply(nb, axis=1)
    return t.sort_values("robust", ascending=False)


