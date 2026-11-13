"""
Strategy family shoot-out on gold. Each family is tuned on TRAIN (2000-2015) only,
then the family winner is picked by TRAIN robust Sharpe — test data is not used to choose.
Every signal is on the bar close, filled at the next open, 3 bp/side costs.
"""
import pandas as pd
from engine import load, score, neighbourhood_pick, FAMILIES

TRAIN_END = pd.Timestamp("2015-12-31")


def main():
    D = load("data/gold_daily.csv")
    first, last = D.index[0], D.index[-1]
    test0 = TRAIN_END + pd.Timedelta(days=1)
    res = []
    for name, (fn, grid) in FAMILIES.items():
        t = neighbourhood_pick(D, fn, grid, first, TRAIN_END)
        best = t.iloc[0]
        p = {k: (bool(best[k]) if k == "shorts" else type(grid[k][0])(best[k])) for k in grid}
        tr, _ = score(D, fn, p, first, TRAIN_END)
        res.append((name, fn, p, tr, best.robust))
        print(f"{name:32s} train robust Sharpe {best.robust:5.2f} | train Sharpe {tr['sharpe']:5.2f} "
              f"return {tr['total']*100:6.0f}% maxDD {tr['maxdd']*100:5.0f}% | params {p}")
    res.sort(key=lambda x: -x[4])
    print(f"\n>>> Winner chosen on TRAIN only: {res[0][0]} {res[0][2]}")
    print("\nUnseen 2016-now, every family (reported for transparency, NOT used for the choice):")
    for name, fn, p, _, _ in res:
        te, _ = score(D, fn, p, test0, last)
        print(f"  {name:32s} return {te['total']*100:6.0f}% | Sharpe {te['sharpe']:5.2f} | maxDD {te['maxdd']*100:5.0f}% "
              f"| in-market {te['exposure']*100:3.0f}%")
    return res


if __name__ == "__main__":
    main()
