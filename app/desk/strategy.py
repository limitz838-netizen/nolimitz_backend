"""
app/desk/strategy.py -- the Desk's trading rules, as pure functions (Oct 2026)

No MT5, no database, no pandas: the live worker (app/desk/worker.py) and the
backtest call exactly these functions, so what was tested is what trades.

Rules (the September gold rules, unchanged):
  BIAS    H4 EMA50 vs EMA200 on closed H4 bars. Long only above, short only below.
  SETUP   On each closed H1 bar: price touched EMA20 within the last 3 H1 bars,
          then this bar closed beyond the previous bar's high (long) / low (short)
          and back on the trend side of EMA20.
  SESSION Entry time (the H1 close) between 07:00 and 20:00 UTC.
  REGIME  H1 ATR14 / median of the last 100 ATR values between 0.6 and 2.0.
  RISK    Stop 1.5 x ATR, target 2.5 x ATR. Breakeven at +1R, then trail
          1 x ATR behind the best price once +1.5R is reached.
  LIMITS  1 open position per symbol. Fixed risk per trade, never increased after
          losses (tested 18 Oct 2026: doubling after losses wiped the account; the
          old 3-entry / 2-loss daily stop blocked only 5 of 520 setups and cost
          3R, so it was removed). The worker keeps a 3% daily-loss circuit breaker.
  EXIT    Close if the H4 bias flips against the position.

Bars are dicts: {"t": <bar open, unix seconds UTC>, "o", "h", "l", "c"}.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import List, Optional

H4_FAST, H4_SLOW = 50, 200
H1_EMA, ATR_LEN, ATR_MEDIAN_LEN = 20, 14, 100
PULLBACK_LOOKBACK = 3
SESSION_START_UTC, SESSION_END_UTC = 7, 20
ATR_RATIO_MIN, ATR_RATIO_MAX = 0.6, 2.0
STOP_ATR, TARGET_ATR = 1.5, 2.5
BREAKEVEN_R, TRAIL_START_R, TRAIL_ATR = 1.0, 1.5, 1.0
MIN_H4_BARS = H4_SLOW + 20
MIN_H1_BARS = ATR_MEDIAN_LEN + ATR_LEN + 10


def ema(values: List[float], n: int) -> List[float]:
    """Same as pandas .ewm(span=n, adjust=False).mean()."""
    if not values:
        return []
    k = 2.0 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def atr(bars: List[dict], n: int = ATR_LEN) -> List[float]:
    """Wilder ATR, same as pandas true-range .ewm(alpha=1/n, adjust=False)."""
    out: List[float] = []
    a = 1.0 / n
    for i, b in enumerate(bars):
        if i == 0:
            tr = b["h"] - b["l"]
        else:
            pc = bars[i - 1]["c"]
            tr = max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
        out.append(tr if i == 0 else tr * a + out[-1] * (1 - a))
    return out


def h4_bias(h4_closed: List[dict]) -> int:
    """+1 / -1 / 0 from closed H4 bars (oldest first)."""
    if len(h4_closed) < MIN_H4_BARS:
        return 0
    closes = [b["c"] for b in h4_closed]
    f, s = ema(closes, H4_FAST)[-1], ema(closes, H4_SLOW)[-1]
    return 1 if f > s else -1 if f < s else 0


@dataclass
class Decision:
    signal: int = 0                 # +1 long, -1 short, 0 nothing
    bias: int = 0
    atr: float = 0.0
    atr_ratio: float = 0.0
    ema20: float = 0.0
    close: float = 0.0
    entry_hour_utc: int = -1
    scout: str = ""                 # one-line reason, shown in the Desk
    checks: dict = field(default_factory=dict)

    @property
    def stop_distance(self) -> float:
        return STOP_ATR * self.atr

    @property
    def target_distance(self) -> float:
        return TARGET_ATR * self.atr


def evaluate(h1_closed: List[dict], bias: int) -> Decision:
    """Decide on the most recent CLOSED H1 bar. h1_closed is oldest first."""
    d = Decision(bias=bias)
    if len(h1_closed) < MIN_H1_BARS:
        d.scout = f"warming up ({len(h1_closed)}/{MIN_H1_BARS} H1 bars)"
        return d
    closes = [b["c"] for b in h1_closed]
    e20 = ema(closes, H1_EMA)
    a = atr(h1_closed)
    last, prev = h1_closed[-1], h1_closed[-2]
    d.atr, d.ema20, d.close = a[-1], e20[-1], last["c"]
    med = median(a[-ATR_MEDIAN_LEN:])
    d.atr_ratio = a[-1] / med if med > 0 else 0.0
    d.entry_hour_utc = int(((last["t"] + 3600) // 3600) % 24)

    recent = h1_closed[-PULLBACK_LOOKBACK:]
    touched_long = min(b["l"] for b in recent) <= d.ema20
    touched_short = max(b["h"] for b in recent) >= d.ema20
    trig_long = last["c"] > prev["h"] and last["c"] > d.ema20
    trig_short = last["c"] < prev["l"] and last["c"] < d.ema20

    d.checks = {
        "bias": bias,
        "session": SESSION_START_UTC <= d.entry_hour_utc < SESSION_END_UTC,
        "regime": ATR_RATIO_MIN <= d.atr_ratio <= ATR_RATIO_MAX,
        "pullback": touched_long if bias > 0 else touched_short if bias < 0 else False,
        "trigger": trig_long if bias > 0 else trig_short if bias < 0 else False,
    }
    side = "long" if bias > 0 else "short"
    if bias == 0:
        d.scout = "no H4 trend (EMA50 = EMA200)"
    elif not d.checks["session"]:
        d.scout = f"outside session ({d.entry_hour_utc:02d}:00 UTC)"
    elif not d.checks["regime"]:
        d.scout = f"ATR regime off ({d.atr_ratio:.2f}x normal)"
    elif not d.checks["pullback"]:
        d.scout = f"H4 {side} bias, waiting for pullback to EMA20"
    elif not d.checks["trigger"]:
        d.scout = f"pullback seen, no {side} trigger yet"
    else:
        d.signal = bias
        d.scout = f"signal: {side}"
    return d


def manage_stop(direction: int, entry: float, stop: float, initial_risk: float,
                best_price: float, atr_at_entry: float) -> Optional[float]:
    """New stop after breakeven / trailing, or None if unchanged.
    best_price = highest high (long) or lowest ask-low (short) since entry."""
    fav = (best_price - entry) * direction
    new = stop
    if fav >= BREAKEVEN_R * initial_risk:
        new = max(new, entry) if direction > 0 else min(new, entry)
    if fav >= TRAIL_START_R * initial_risk:
        trail = best_price - direction * TRAIL_ATR * atr_at_entry
        new = max(new, trail) if direction > 0 else min(new, trail)
    return new if abs(new - stop) > 1e-12 else None
