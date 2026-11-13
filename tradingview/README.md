# Gold Trend Rider — TradingView indicator + honest backtest

| File | What it is |
|---|---|
| `gold_trend_rider.pine` | **The indicator.** BUY / EXIT labels, EMA ribbon, status panel, alerts. Paste into TradingView → Pine Editor → *Add to chart*. |
| `gold_trend_rider_strategy.pine` | Same rules as a `strategy()` so you can verify in TradingView's Strategy Tester. |
| `strategy_shootout.py` | Tunes 4 well-known gold strategy families on **2000–2015 only** and picks the winner. |
| `validate.py` | Unseen-data test, coin-flip Monte Carlo, walk-forward, 1-hour test. |
| `engine.py` | Shared simulator (signal on bar close → fill next open, 0.03 %/side costs). |
| `fetch_data.py` | Re-downloads gold futures data (GC=F) into `data/`. |

```bash
cd tradingview && python3 strategy_shootout.py && python3 validate.py
```

## The rules

- **BUY**: on a closed daily bar, the 50 EMA is above the 200 EMA. Enter at the next open.
- **EXIT**: on a closed daily bar, the 50 EMA is below the 200 EMA. Close at the next open.
- **Shorts** are off by default. Shorting gold lost money on 2000–2015 in every strategy family tested.

None of the signals repaint. **Setting up alerts:** in TradingView open *Create Alert*, set Condition to *Gold Trend Rider*, then choose either:

- a specific alert, such as "Gold BUY" or "Gold EXIT long", or
- "Any alert() function call", which gives you one alert for every signal.

## How it was chosen (no peeking at the test years)

The four families were each tuned on 2000-08 → 2015-12 daily gold futures. Picks were ranked by the *average* Sharpe of each config's neighbouring parameters, so a lucky spike couldn't win.

| Family (best params on train) | Train Sharpe (neighbourhood avg) |
|---|---|
| **EMA crossover 50/200, long only** ✅ | **0.58** |
| 12-month time-series momentum | 0.53 |
| Donchian 10-bar breakout + 3×ATR trail | 0.49 |
| RSI(2) pullback in uptrend | 0.31 |

## Results on data it had never seen (2016-01 → 2026-11, daily)

| | Return | CAGR | Sharpe | Max DD | Time in market |
|---|---|---|---|---|---|
| 2000–2015 (seen, train) | +349 % | 10.4 % | 0.73 | −23 % | 69 % |
| **2016–2026 (unseen)** | **+108 %** | **7.0 %** | **0.51** | **−34 %** | 78 % |
| Buy & hold gold, same span | +261 % | 12.6 % | 0.78 | −27 % | 100 % |

### Coin-flip test (5,000 runs on the unseen period)

Each run had the same number of trades, the same holding lengths and the same time in the market. Only the timing and direction were random.

| Baseline | Median return | Strategy beats | p-value (Sharpe) |
|---|---|---|---|
| 50/50 long-or-short coin | −11 % | 86 % of runs | 0.15 |
| Random timing, long only | +197 % | 5.5 % of runs | 0.96 |

### Walk-forward

Every 2 years, the family *and* its parameters were re-picked using only past data, then traded blind for the next 2 years. Stitched together, 2008–2026 returned +75 % (Sharpe 0.27, max DD −42 %). Buy & hold returned +353 % (Sharpe 0.53, max DD −44 %) over the same span.

### Timeframe it never saw: 1-hour bars, Jun 2024 → Nov 2026

The daily parameters were not re-tuned for hourly bars.

| | Result |
|---|---|
| Strategy | +37.7 %, Sharpe 0.88, 40 trades, profit factor 1.98, max DD −27 % |
| Buy & hold, same span | +60.8 % |

## What this honestly means

1. **It beats a coin flip on direction, but not significantly** (p = 0.15). The edge comes from being long gold, not from clever timing.
2. **Its timing was worse than random.** Random long entries with the same exposure beat it 94 % of the time on unseen data. The 200 EMA reacts late, so it often enters after the move and exits after the drop.
3. **No strategy we tested beat buy & hold on gold out of sample.** That includes breakout, mean-reversion, momentum and moving-average crossovers, over 2016–2026, which was a strong bull market for gold.
4. **What it does well is keep you out of long bear phases.** In 2012–2015 it lost about 7 % while gold fell 35 %. It also stays aligned with the major trend, and it's simple enough that it's unlikely to be curve-fitted.

Data is GC=F continuous futures from Yahoo Finance. The early-2000s bars are thin, and spot XAUUSD from your broker will differ slightly. Past results don't guarantee future returns. This is not financial advice.
