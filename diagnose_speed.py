"""
================================================================================
  NOLIMITZ — SPEED DIAGNOSTIC  (read-only, safe to run anytime)
================================================================================
  Finds WHERE the 9-minute watcher cycle goes. Measures, in isolation:
    1. One round-trip to the Render Postgres (SELECT 1)
    2. A realistic AIMarketState query (what the watcher does per symbol)
    3. How many DB connections are currently open on the server
    4. One MT5 copy_rates fetch per timeframe (what the watcher does per symbol)
    5. A full simulated single-symbol scan cost (DB read + 3 candle fetches)

  It WRITES NOTHING and TRADES NOTHING. Pure measurement. Run it from the
  same folder you run the workers from:

      py -3.10 diagnose_speed.py

  Read the summary at the bottom — it names the culprit in plain English.
================================================================================
"""

import time
import statistics

# ── Reuse the app's own DB + MT5 exactly as the workers do ──────────────────
from sqlalchemy import text
from app.database import SessionLocal, engine, DATABASE_URL

try:
    import MetaTrader5 as mt5
    HAVE_MT5 = True
except Exception:
    HAVE_MT5 = False


def _fmt(seconds: float) -> str:
    if seconds >= 1:
        return f"{seconds:.2f}s"
    return f"{seconds*1000:.0f}ms"


def time_it(fn, runs=5):
    """Run fn() `runs` times, return (median, min, max, all)."""
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        try:
            fn()
        except Exception as e:
            return None, None, None, str(e)
        samples.append(time.perf_counter() - t0)
    return statistics.median(samples), min(samples), max(samples), samples


print("=" * 72)
print("  NOLIMITZ SPEED DIAGNOSTIC")
print("=" * 72)

# Show which DB host we're hitting (mask the password)
try:
    safe_url = DATABASE_URL
    if "@" in safe_url and "://" in safe_url:
        head, tail = safe_url.split("://", 1)
        creds, host = tail.split("@", 1)
        user = creds.split(":", 1)[0]
        safe_url = f"{head}://{user}:****@{host}"
    print(f"\nDatabase: {safe_url}")
except Exception:
    pass


# ── TEST 1: bare round-trip to Postgres ─────────────────────────────────────
print("\n" + "-" * 72)
print("TEST 1 — Bare DB round-trip (SELECT 1)")
print("-" * 72)

def bare_select():
    with SessionLocal() as db:
        db.execute(text("SELECT 1")).scalar()

med1, mn1, mx1, samples1 = time_it(bare_select, runs=5)
if med1 is None:
    print(f"  ❌ FAILED: {samples1}")
else:
    print(f"  median={_fmt(med1)}  min={_fmt(mn1)}  max={_fmt(mx1)}")
    print(f"  (this is pure network latency to the database, one query)")


# ── TEST 2: realistic per-symbol query the watcher runs ─────────────────────
print("\n" + "-" * 72)
print("TEST 2 — Watcher's per-symbol query (AIMarketState for XAUUSD)")
print("-" * 72)

def market_state_query():
    with SessionLocal() as db:
        db.execute(text(
            "SELECT id, symbol, signal, confidence, updated_at "
            "FROM ai_market_states WHERE symbol = :s LIMIT 1"
        ), {"s": "XAUUSD"}).first()

med2, mn2, mx2, samples2 = time_it(market_state_query, runs=5)
if med2 is None:
    print(f"  ❌ FAILED: {samples2}")
else:
    print(f"  median={_fmt(med2)}  min={_fmt(mn2)}  max={_fmt(mx2)}")


# ── TEST 3: how many connections are open on the server right now ────────────
print("\n" + "-" * 72)
print("TEST 3 — Open DB connections on the server (are workers saturating it?)")
print("-" * 72)

try:
    with SessionLocal() as db:
        total = db.execute(text(
            "SELECT count(*) FROM pg_stat_activity"
        )).scalar()
        by_state = db.execute(text(
            "SELECT state, count(*) FROM pg_stat_activity GROUP BY state"
        )).fetchall()
        max_conn = db.execute(text("SHOW max_connections")).scalar()
    print(f"  total connections open: {total} / max {max_conn}")
    for state, cnt in by_state:
        print(f"    {state or 'unknown'}: {cnt}")
    if int(total) > int(max_conn) * 0.7:
        print("  ⚠️  connection pool is heavily used — workers may be queueing")
except Exception as e:
    print(f"  (couldn't read pg_stat_activity: {e})")


# ── TEST 4 & 5: MT5 candle fetches (the other half of a scan) ───────────────
print("\n" + "-" * 72)
print("TEST 4 — MT5 candle fetches (copy_rates per timeframe)")
print("-" * 72)

if not HAVE_MT5:
    print("  MetaTrader5 not importable here — skipping MT5 tests.")
    mt5_ok = False
else:
    import os
    term = os.environ.get("MT5_TERMINAL_PATH")
    init_ok = mt5.initialize(path=term) if term else mt5.initialize()
    if not init_ok:
        print(f"  ❌ MT5 initialize failed: {mt5.last_error()}")
        print(f"  (set MT5_TERMINAL_PATH first, same as the worker)")
        mt5_ok = False
    else:
        mt5_ok = True
        # Resolve a real gold symbol name on this broker
        broker_sym = None
        for cand in ("XAUUSD", "GOLD", "XAUUSDc", "XAUUSDm"):
            if mt5.symbol_info(cand):
                broker_sym = cand
                break
        if not broker_sym:
            syms = mt5.symbols_get() or []
            for s in syms:
                if "XAU" in s.name.upper() or "GOLD" in s.name.upper():
                    broker_sym = s.name
                    break
        print(f"  gold symbol on this broker: {broker_sym}")
        if broker_sym:
            mt5.symbol_select(broker_sym, True)
            tfs = [("M15", mt5.TIMEFRAME_M15, 300),
                   ("H1",  mt5.TIMEFRAME_H1,  200),
                   ("H4",  mt5.TIMEFRAME_H4,  150)]
            per_tf = {}
            for name, tf, n in tfs:
                def fetch(tf=tf, n=n):
                    r = mt5.copy_rates_from_pos(broker_sym, tf, 0, n)
                    if r is None:
                        raise RuntimeError(f"copy_rates returned None ({mt5.last_error()})")
                med, mn, mx, s = time_it(fetch, runs=3)
                if med is None:
                    print(f"    {name}: ❌ {s}")
                    per_tf[name] = None
                else:
                    print(f"    {name} ({n} candles): median={_fmt(med)}  max={_fmt(mx)}")
                    per_tf[name] = med


# ── TEST 5: total cost of ONE simulated symbol scan ─────────────────────────
print("\n" + "-" * 72)
print("TEST 5 — Estimated cost of ONE symbol scan (DB read + 3 candle fetches)")
print("-" * 72)

db_cost = med2 if med2 else 0
mt5_cost = 0
if HAVE_MT5 and 'per_tf' in dir() and per_tf:
    mt5_cost = sum(v for v in per_tf.values() if v)
one_symbol = db_cost + mt5_cost
print(f"  DB read:        {_fmt(db_cost)}")
print(f"  MT5 fetches:    {_fmt(mt5_cost)}")
print(f"  → per symbol:   {_fmt(one_symbol)}")
print(f"  → for 15 symbols (rough): {_fmt(one_symbol * 15)}")


# ── VERDICT ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("  VERDICT")
print("=" * 72)

verdicts = []
if med1 is not None:
    if med1 > 0.5:
        verdicts.append(
            f"🔴 DATABASE IS THE BOTTLENECK. A bare SELECT 1 takes {_fmt(med1)} — "
            f"that's network latency to Render's Postgres. Every query in every "
            f"cycle pays this. With many queries per cycle across 3 workers, this "
            f"alone can produce multi-minute cycles.\n"
            f"     FIX DIRECTION: run the workers CLOSER to the database (a small "
            f"VPS in the same region as Render's Postgres, or Render's own "
            f"background workers), OR cut the number of queries per cycle."
        )
    elif med1 > 0.15:
        verdicts.append(
            f"🟡 Database round-trip is {_fmt(med1)} — noticeably slow but not "
            f"catastrophic. It adds up across many queries; worth reducing query "
            f"count per cycle."
        )
    else:
        verdicts.append(
            f"🟢 Database round-trip is {_fmt(med1)} — healthy. The DB is NOT the "
            f"main bottleneck."
        )

if HAVE_MT5 and 'mt5_ok' in dir() and mt5_ok and mt5_cost:
    if mt5_cost > 3:
        verdicts.append(
            f"🔴 MT5 IS SLOW. Three candle fetches for one symbol take {_fmt(mt5_cost)}. "
            f"Across 15 symbols that's ~{_fmt(mt5_cost*15)} of MT5 waiting per cycle.\n"
            f"     FIX DIRECTION: the MT5 terminal or its broker connection is "
            f"struggling — check the terminal's connection, or reduce candles/"
            f"timeframes fetched per symbol."
        )
    else:
        verdicts.append(
            f"🟢 MT5 fetches are {_fmt(mt5_cost)} per symbol — healthy."
        )

if not verdicts:
    verdicts.append("Couldn't measure enough to conclude — see individual tests above.")

for v in verdicts:
    print("\n  " + v)

print("\n" + "=" * 72)
print("  Done. Nothing was written or traded. Paste this whole output back.")
print("=" * 72)

try:
    if HAVE_MT5 and 'mt5_ok' in dir() and mt5_ok:
        mt5.shutdown()
except Exception:
    pass