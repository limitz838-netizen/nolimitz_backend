"""
app/desk/worker.py -- the Desk trader (Oct 2026)

Run on the VPS, in its OWN terminal (never one a shard, the watcher, the
verifier or the copier uses):

    py -3.10 -m app.desk.worker

It logs in to ONE MT5 account (the Desk account), trades the rules in
app/desk/strategy.py on each closed H1 candle, manages the stops, and writes
what every agent is doing to desk_events / desk_trades / desk_state so the
Desk page can show it live.

Settings (environment variables; none of these names are in .env.local, so a
launcher .bat can set them per process):
  DESK_WORKER_NAME        desk-1
  DESK_MT5_LOGIN          required
  DESK_MT5_PASSWORD       required (kept in memory only, never written anywhere)
  DESK_MT5_SERVER         required, e.g. Exness-MT5Trial10
  DESK_TERMINAL_PATH      required, e.g. C:\\MT5\\desk\\terminal64.exe
  DESK_PORTABLE           true
  DESK_SYMBOLS            XAUUSD            (comma list, e.g. XAUUSD,BTCUSD)
  DESK_SHADOW_SYMBOLS     (empty)           (symbols that NEVER send orders, even when trading is
                                             enabled - e.g. BTCUSD until it passes its own backtest)
  DESK_STRATEGIES         XAUUSD=pullback   (per-symbol rules from app/desk/strategy.py:
                                             pullback = gold rules, squeeze = BTC idea C)
  DESK_TRADING_ENABLED    false             (false = shadow mode: full pipeline, no orders)
  DESK_ALLOW_REAL         false             (refuses to trade a non-demo account unless true)
  DESK_RISK_PCT           1.0               (risk per trade, % of balance; capped at 2)
  DESK_DAILY_LOSS_PCT     3.0               (no new entries after this % loss in a UTC day)
  DESK_MAX_SPREAD_ATR     0.08              (skip if spread > this fraction of H1 ATR)
  DESK_MAGIC              78001
  DESK_LOOP_SEC           5
  DESK_HEARTBEAT_FILE     (set by run_desk.ps1) touched every loop; the launcher restarts a frozen Desk
  DESK_LOCK_PORT          47811             (only ONE Desk may run: a second copy exits with code 3)

Shadow symbols are PAPER-TRADED: each shadow signal opens a paper position (one at a
time per symbol) that is managed on the broker's live prices - stop, target, breakeven -
and recorded in desk_trades with a ticket starting "paper-". No order is ever sent for
it. That builds a forward-only record a backtest cannot give.

Who copies these trades: nobody, unless this account is also set as a master
in master_mt5_bridge. Keep it that way until the demo forward test passes.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import SessionLocal
from app.desk import strategy as S
from app.desk.models import DeskEvent, DeskState, DeskTrade

try:
    from app.models import WorkerHeartbeat
except Exception:  # VPS models.py has drifted before; heartbeat is optional
    WorkerHeartbeat = None

mt5 = None  # set in main(); tests inject a fake

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("desk")


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _flag(name, default="false"):
    return _env(name, default).lower() in ("1", "true", "yes", "on")


class Cfg:
    WORKER = _env("DESK_WORKER_NAME", "desk-1")
    LOGIN = int(_env("DESK_MT5_LOGIN", "0") or 0)
    PASSWORD = os.environ.get("DESK_MT5_PASSWORD", "")
    SERVER = _env("DESK_MT5_SERVER")
    TERMINAL = _env("DESK_TERMINAL_PATH")
    PORTABLE = _flag("DESK_PORTABLE", "true")
    SYMBOLS = [s.strip().upper() for s in _env("DESK_SYMBOLS", "XAUUSD").split(",") if s.strip()]
    TRADING_ENABLED = _flag("DESK_TRADING_ENABLED", "false")
    SHADOW_SYMBOLS = {s.strip().upper() for s in _env("DESK_SHADOW_SYMBOLS", "").split(",") if s.strip()}
    STRATEGIES = {k.strip().upper(): v.strip().lower() for k, v in
                  (x.split("=", 1) for x in _env("DESK_STRATEGIES", "XAUUSD=pullback").split(",") if "=" in x)}
    ALLOW_REAL = _flag("DESK_ALLOW_REAL", "false")
    RISK_PCT = min(2.0, max(0.1, float(_env("DESK_RISK_PCT", "1.0"))))
    DAILY_LOSS_PCT = float(_env("DESK_DAILY_LOSS_PCT", "3.0"))
    MAX_SPREAD_ATR = float(_env("DESK_MAX_SPREAD_ATR", "0.08"))
    MAGIC = int(_env("DESK_MAGIC", "78001"))
    LOOP_SEC = float(_env("DESK_LOOP_SEC", "5"))
    HEARTBEAT_FILE = _env("DESK_HEARTBEAT_FILE")
    LOCK_PORT = int(_env("DESK_LOCK_PORT", "47811") or 0)
    DEVIATION = 50
    H4_BARS, H1_BARS, M15_CANDLES = 1000, 500, 64
    LATE_SIGNAL_SEC = 300          # never act on an H1 close older than this
    EVENT_KEEP_DAYS = 30


ALIASES = {
    "XAUUSD": ["XAUUSD", "XAUUSDm", "XAUUSDc", "XAUUSD.m", "GOLD", "GOLDm"],
    "BTCUSD": ["BTCUSD", "BTCUSDm", "BTCUSDc", "BTCUSD.m", "BTCUSDT"],
}


def now_utc() -> datetime:  # tests replace this
    return datetime.now(timezone.utc)


def sleep(sec: float) -> None:  # tests replace this
    time.sleep(sec)


def fmt(v: float, d: int = 2) -> str:
    return f"{v:,.{d}f}"


def mask_login(login) -> str:
    s = str(login or "")
    return s[:2] + "***" + s[-3:] if len(s) > 5 else "***"


# =============================================================================
class Desk:
    def __init__(self, cfg=Cfg):
        self.cfg = cfg
        self.db = SessionLocal()
        self.sym: Dict[str, dict] = {}          # canonical -> {broker, digits, point, ...}
        self.last_h1: Dict[str, int] = {}        # canonical -> last evaluated H1 bar open (utc)
        self.bias: Dict[str, int] = {}
        self.decision: Dict[str, S.Decision] = {}
        self.agents = {a: "starting" for a in ("scout", "check", "size", "entry", "exit", "guard")}
        self.offset_sec = 0                      # broker server time minus UTC
        self.offset_known = False
        self.tick_seen: Dict[str, float] = {}     # canonical -> last tick time seen (to spot live markets)
        self.offset_pending = (None, 0)           # (candidate offset, consecutive confirmations)
        self.day = None
        self.day_start_balance = None
        self.last_beat = 0.0
        self.last_prune = 0.0
        self.warned = set()
        self.closing_reason: Dict[str, str] = {}  # ticket -> reason we closed it for
        self.paper: Dict[str, dict] = {}          # paper ticket -> {seen (utc ts of next bar), sl, best}

    # ---------------------------------------------------------------- db ---
    def _commit(self):
        try:
            self.db.commit()
        except Exception as e:
            log.warning("db commit failed: %s", e)
            try:
                self.db.rollback()
            except Exception:
                pass
            try:
                self.db.close()
            except Exception:
                pass
            self.db = SessionLocal()

    def event(self, agent: str, message: str, symbol: Optional[str] = None, level: str = "info", data=None):
        if agent in self.agents:
            self.agents[agent] = message
        log.info("[%s]%s %s", agent.upper(), f" {symbol}" if symbol else "", message)
        try:
            self.db.add(DeskEvent(worker=self.cfg.WORKER, symbol=symbol, agent=agent, level=level,
                                  message=message[:240], data=data))
            self._commit()
        except Exception as e:
            log.warning("event write failed: %s", e)
            self._commit()

    def warn_once(self, key: str, agent: str, message: str, symbol=None, level="warn"):
        if key in self.warned:
            return
        self.warned.add(key)
        self.event(agent, message, symbol, level)

    # --------------------------------------------------------------- mt5 ---
    def connect(self) -> bool:
        c = self.cfg
        if not (c.LOGIN and c.PASSWORD and c.SERVER and c.TERMINAL):
            log.critical("Set DESK_MT5_LOGIN, DESK_MT5_PASSWORD, DESK_MT5_SERVER and DESK_TERMINAL_PATH.")
            return False
        try:
            mt5.shutdown()
        except Exception:
            pass
        if not mt5.initialize(path=c.TERMINAL, portable=c.PORTABLE, timeout=60000):
            log.error("MT5 init failed (%s): %s", c.TERMINAL, mt5.last_error())
            return False
        if not mt5.login(c.LOGIN, password=c.PASSWORD, server=c.SERVER, timeout=30000):
            log.error("MT5 login failed for %s @ %s: %s", mask_login(c.LOGIN), c.SERVER, mt5.last_error())
            return False
        info = mt5.account_info()
        if not info or info.login != c.LOGIN:
            log.error("MT5 logged in to the wrong account")
            return False
        log.info("Desk connected: %s @ %s (%s)", mask_login(info.login), info.server,
                 "DEMO" if self.is_demo(info) else "REAL")
        self.sym.clear()
        return True

    def force_kill(self):
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-Process terminal64 -ErrorAction SilentlyContinue | "
                            f"Where-Object {{ $_.Path -eq '{self.cfg.TERMINAL}' }} | Stop-Process -Force"],
                           timeout=20, capture_output=True)
            log.warning("force-killed desk terminal %s", self.cfg.TERMINAL)
        except Exception as e:
            log.warning("force-kill failed: %s", e)

    def ensure_connected(self) -> bool:
        info = mt5.account_info()
        if info and info.login == self.cfg.LOGIN:
            return True
        self.event("guard", "MT5 disconnected \u00b7 reconnecting", level="warn")
        for attempt in range(3):
            if self.connect():
                self.event("guard", "MT5 reconnected", level="ok")
                return True
            sleep(10 * (attempt + 1))
        self.force_kill()
        sleep(30)
        return self.connect()

    def strategy_of(self, canon: str) -> str:
        name = self.cfg.STRATEGIES.get(canon, "pullback")
        return name if name in S.STRATEGIES else "pullback"

    def shadow(self, canon: str) -> bool:
        return (not self.cfg.TRADING_ENABLED) or canon in self.cfg.SHADOW_SYMBOLS

    def open_paper(self, canon: str):
        return (self.db.query(DeskTrade).filter(DeskTrade.worker == self.cfg.WORKER, DeskTrade.symbol == canon,
                                                 DeskTrade.is_open.is_(True), DeskTrade.ticket.like("paper-%")).first())

    @staticmethod
    def is_demo(info) -> bool:
        return getattr(info, "trade_mode", 2) == getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)

    def resolve(self, canon: str) -> Optional[dict]:
        if canon in self.sym:
            return self.sym[canon]
        names = ALIASES.get(canon, [canon])
        found = None
        for n in names:
            if mt5.symbol_info(n) is not None:
                found = n
                break
        if not found:
            for s in mt5.symbols_get() or []:
                up = s.name.upper()
                if up.startswith(canon[:3]) and canon[3:] in up:
                    found = s.name
                    break
        if not found:
            return None
        mt5.symbol_select(found, True)
        i = mt5.symbol_info(found)
        self.sym[canon] = {"broker": found, "digits": i.digits, "point": i.point,
                           "vmin": i.volume_min, "vmax": i.volume_max, "vstep": i.volume_step,
                           "stops": i.trade_stops_level, "filling": i.filling_mode}
        log.info("symbol %s -> %s", canon, found)
        return self.sym[canon]

    def bars(self, broker: str, tf, start_pos: int, count: int) -> List[dict]:
        r = mt5.copy_rates_from_pos(broker, tf, start_pos, count)
        if r is None:
            return []
        off = self.offset_sec
        return [{"t": int(x["time"]) - off, "o": float(x["open"]), "h": float(x["high"]),
                 "l": float(x["low"]), "c": float(x["close"])} for x in r]

    def market_open(self, broker: str, tick) -> bool:
        if tick is None:
            return False
        info = mt5.symbol_info(broker)
        full = getattr(mt5, "SYMBOL_TRADE_MODE_FULL", 4)
        if info is None or info.trade_mode != full:
            return False
        age = now_utc().timestamp() - (tick.time - self.offset_sec)
        return age < 180

    def learn_offset(self, ticks: Dict[str, object]):
        """Broker server time minus UTC, rounded to the hour (Exness is 0).
        Only a LIVE tick can teach it: one whose time moved since the last loop. A closed market's
        frozen last tick (gold at the weekend) would otherwise read as a -43h offset. Real broker
        offsets are between UTC-12 and UTC+14, and a change must repeat on 3 loops before it is used."""
        live = []
        for canon, tick in ticks.items():
            if tick is None:
                continue
            prev = self.tick_seen.get(canon)
            self.tick_seen[canon] = tick.time
            if prev is not None and tick.time != prev:
                live.append(tick)
        if not live:
            return
        tick = max(live, key=lambda t: t.time)
        diff = tick.time - now_utc().timestamp()
        hours = round(diff / 3600)
        if abs(diff - hours * 3600) >= 300 or not -12 <= hours <= 14:
            return
        new = int(hours * 3600)
        if self.offset_known and new == self.offset_sec:
            self.offset_pending = (None, 0)
            return
        cand, n = self.offset_pending
        n = n + 1 if cand == new else 1
        self.offset_pending = (new, n)
        if not self.offset_known or n >= 3:
            self.offset_sec, self.offset_known = new, True
            self.offset_pending = (None, 0)
            log.info("broker time offset: UTC%+d", hours)

    # ------------------------------------------------------------- orders ---
    def _send(self, req: dict):
        fillings = [getattr(mt5, n) for n in ("ORDER_FILLING_FOK", "ORDER_FILLING_IOC", "ORDER_FILLING_RETURN")]
        res = None
        for f in fillings:
            req["type_filling"] = f
            res = mt5.order_send(req)
            done = getattr(mt5, "TRADE_RETCODE_DONE", 10009)
            if res is not None and res.retcode in (done, getattr(mt5, "TRADE_RETCODE_PLACED", 10008)):
                return res
            if res is not None and res.retcode != getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030):
                return res
        return res

    def close_position(self, pos, reason: str) -> bool:
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return False
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
               "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
               "position": pos.ticket, "price": tick.bid if is_buy else tick.ask,
               "deviation": self.cfg.DEVIATION, "magic": self.cfg.MAGIC, "comment": f"desk {reason}"[:31],
               "type_time": mt5.ORDER_TIME_GTC}
        res = self._send(req)
        ok = res is not None and res.retcode == getattr(mt5, "TRADE_RETCODE_DONE", 10009)
        if ok:
            self.closing_reason[str(pos.ticket)] = reason
        return ok

    def modify_stop(self, pos, new_sl: float) -> bool:
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol, "position": pos.ticket,
               "sl": new_sl, "tp": pos.tp, "magic": self.cfg.MAGIC}
        res = mt5.order_send(req)
        return res is not None and res.retcode == getattr(mt5, "TRADE_RETCODE_DONE", 10009)

    def our_positions(self) -> list:
        return [p for p in (mt5.positions_get() or []) if p.magic == self.cfg.MAGIC]

    # ------------------------------------------------------------- day/guard ---
    def roll_day(self, info):
        today = now_utc().date()
        if self.day != today:
            self.day = today
            self.day_start_balance = info.balance
            if self.day is not None:
                self.event("chief", f"new UTC day {today} \u00b7 start balance {fmt(info.balance)} {info.currency}")

    def day_counts(self):
        start = datetime.combine(self.day, datetime.min.time(), tzinfo=timezone.utc)
        rows = (self.db.query(DeskTrade).filter(DeskTrade.worker == self.cfg.WORKER,
                                                 DeskTrade.opened_at >= start).all())
        entries = len(rows)
        losses = sum(1 for r in rows if not r.is_open and (r.profit_usd or 0) < 0)
        return entries, losses

    def guard_blocks(self, info, canon: str) -> Optional[str]:
        if not self.is_demo(info) and not self.cfg.ALLOW_REAL:
            return "real account \u00b7 Desk only trades demo until DESK_ALLOW_REAL=true"
        if self.day_start_balance:
            dd = (info.equity - self.day_start_balance) / self.day_start_balance * 100
            if dd <= -self.cfg.DAILY_LOSS_PCT:
                return f"daily loss {dd:.1f}% hit the {self.cfg.DAILY_LOSS_PCT}% limit"
        broker = self.sym[canon]["broker"]
        if any(p.symbol == broker for p in self.our_positions()):
            return f"already holding {canon}"
        if self.shadow(canon) and self.open_paper(canon) is not None:
            return f"paper {canon} position still open"
        return None

    # ------------------------------------------------------------ pipeline ---
    def on_h1_close(self, canon: str, info, h1: List[dict], h4: List[dict], act: bool):
        s = self.sym[canon]
        bias = S.h4_bias(h4)
        self.bias[canon] = bias
        d = S.STRATEGIES[self.strategy_of(canon)](h1, bias)
        self.decision[canon] = d
        bar_t = datetime.fromtimestamp(h1[-1]["t"] + 3600, timezone.utc).strftime("%H:%M")
        self.event("scout", f"H1 {bar_t} UTC \u00b7 {d.scout}", canon, "ok" if d.signal else "info",
                   {"strategy": d.strategy, "bias": bias, "atr": round(d.atr, 3), "atr_ratio": round(d.atr_ratio, 2),
                    "ema20": round(d.ema20, s["digits"]), "close": d.close, "checks": d.checks})
        if not d.signal:
            return
        side = "long" if d.signal > 0 else "short"
        if not act:
            self.event("guard", f"{canon} {side} signal is from before startup \u00b7 not traded", canon, "warn")
            return

        blocked = self.guard_blocks(info, canon)
        if blocked:
            self.event("guard", f"{canon} {side} blocked \u00b7 {blocked}", canon, "warn")
            return

        tick = mt5.symbol_info_tick(s["broker"])
        if not self.market_open(s["broker"], tick):
            self.event("check", f"{canon} market closed or no fresh price", canon, "warn")
            return
        spread = tick.ask - tick.bid
        if spread > self.cfg.MAX_SPREAD_ATR * d.atr:
            self.event("check", f"{canon} spread {fmt(spread, s['digits'])} too wide "
                                f"({spread / d.atr:.0%} of ATR)", canon, "warn")
            return
        self.event("check", f"{canon} spread {fmt(spread, s['digits'])} \u00b7 {spread / d.atr:.0%} of ATR \u00b7 ok", canon, "ok")

        # ---- size: risk RISK_PCT of balance over the stop distance
        price = tick.ask if d.signal > 0 else tick.bid
        otype = mt5.ORDER_TYPE_BUY if d.signal > 0 else mt5.ORDER_TYPE_SELL
        stop_px = price - d.signal * d.stop_distance
        loss1 = mt5.order_calc_profit(otype, s["broker"], 1.0, price, stop_px)
        if not loss1:
            self.event("size", f"{canon} could not price the stop \u00b7 skipped", canon, "error")
            return
        risk_usd = info.balance * self.cfg.RISK_PCT / 100
        raw = risk_usd / abs(loss1)
        lot = int(raw / s["vstep"] + 1e-9) * s["vstep"]
        lot = round(min(s["vmax"], lot), 2)
        if lot < s["vmin"]:
            min_risk = abs(loss1) * s["vmin"]
            if min_risk > risk_usd * 1.5:
                self.event("size", f"{canon} min lot {s['vmin']} risks ${fmt(min_risk)} > "
                                   f"{self.cfg.RISK_PCT}% \u00b7 skipped", canon, "warn")
                return
            lot = s["vmin"]
        margin = mt5.order_calc_margin(otype, s["broker"], lot, price)
        if margin is not None and margin > info.margin_free * 0.5:
            self.event("size", f"{canon} {lot} lots needs ${fmt(margin)} margin \u00b7 too much \u00b7 skipped", canon, "warn")
            return
        real_risk = abs(loss1) * lot
        self.event("size", f"{canon} {lot} lots \u00b7 risk ${fmt(real_risk)} "
                           f"({real_risk / info.balance:.1%}) \u00b7 stop {fmt(d.stop_distance, s['digits'])}",
                   canon, "ok", {"lot": lot, "risk_usd": round(real_risk, 2), "loss_per_lot": loss1})

        sl = round(stop_px, s["digits"])
        tp = round(price + d.signal * d.target_distance, s["digits"])
        if self.shadow(canon):
            ticket = "paper-%d%s" % (int(now_utc().timestamp()), canon[:3])
            self.db.add(DeskTrade(worker=self.cfg.WORKER, ticket=ticket, symbol=canon, broker_symbol=s["broker"],
                                  direction=d.signal, lot=lot, entry_price=price, initial_stop=sl, current_stop=sl,
                                  target=tp, atr_at_entry=d.atr, risk_usd=real_risk, opened_at=now_utc(),
                                  is_demo=self.is_demo(info)))
            self._commit()
            self.event("entry", f"PAPER {side} {canon} {lot} @ {fmt(price, s['digits'])} \u00b7 "
                                f"SL {fmt(sl, s['digits'])} \u00b7 TP {fmt(tp, s['digits'])} \u00b7 no order sent",
                       canon, "warn", {"shadow": True, "paper": True, "ticket": ticket, "price": price,
                                       "sl": sl, "tp": tp, "lot": lot, "strategy": d.strategy})
            return

        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": s["broker"], "volume": lot, "type": otype,
               "price": price, "sl": sl, "tp": tp, "deviation": self.cfg.DEVIATION, "magic": self.cfg.MAGIC,
               "comment": f"desk {side}", "type_time": mt5.ORDER_TIME_GTC}
        res = self._send(req)
        if res is None or res.retcode != getattr(mt5, "TRADE_RETCODE_DONE", 10009):
            self.event("entry", f"{canon} order rejected \u00b7 {getattr(res, 'retcode', '?')} "
                                f"{getattr(res, 'comment', mt5.last_error())}", canon, "error")
            return
        pos = None
        for p in self.our_positions():
            if p.symbol == s["broker"] and (p.ticket == res.order or p.identifier == res.order):
                pos = p
        if pos is None:
            cands = [p for p in self.our_positions() if p.symbol == s["broker"]]
            pos = max(cands, key=lambda p: p.time_msc) if cands else None
        fill = pos.price_open if pos else (res.price or price)
        ticket = str(pos.ticket if pos else res.order)
        self.db.add(DeskTrade(worker=self.cfg.WORKER, ticket=ticket, symbol=canon, broker_symbol=s["broker"],
                              direction=d.signal, lot=lot, entry_price=fill,
                              initial_stop=pos.sl if pos and pos.sl else sl, current_stop=pos.sl if pos and pos.sl else sl,
                              target=pos.tp if pos and pos.tp else tp, atr_at_entry=d.atr, risk_usd=real_risk,
                              opened_at=now_utc(), is_demo=self.is_demo(info)))
        self._commit()
        self.event("entry", f"{side.upper()} {canon} {lot} @ {fmt(fill, s['digits'])} \u00b7 "
                            f"SL {fmt(sl, s['digits'])} \u00b7 TP {fmt(tp, s['digits'])}",
                   canon, "ok", {"ticket": ticket, "lot": lot, "price": fill, "sl": sl, "tp": tp})

    # ---------------------------------------------------------- management ---
    def manage(self, canon: str):
        s = self.sym[canon]
        rows = (self.db.query(DeskTrade).filter(DeskTrade.worker == self.cfg.WORKER, DeskTrade.symbol == canon,
                                                 DeskTrade.is_open.is_(True)).all())
        live = {str(p.ticket): p for p in self.our_positions() if p.symbol == s["broker"]}
        rules = self.strategy_of(canon)
        for row in rows:
            if row.ticket.startswith("paper-"):
                self.manage_paper(row)
                continue
            pos = live.get(row.ticket)
            if pos is None:
                self.record_close(row)
                continue
            # H4 bias flip against us -> close (pullback rules only)
            if rules == "pullback" and self.bias.get(canon, 0) == -row.direction:
                if self.close_position(pos, "bias flip"):
                    self.event("exit", f"{canon} H4 trend flipped \u00b7 closing", canon, "warn")
                continue
            # best price since entry -> breakeven / trail
            opened = row.opened_at if row.opened_at.tzinfo else row.opened_at.replace(tzinfo=timezone.utc)
            m15 = mt5.copy_rates_range(s["broker"], mt5.TIMEFRAME_M15,
                                       opened + timedelta(seconds=self.offset_sec),
                                       now_utc() + timedelta(seconds=self.offset_sec + 900))
            tick = mt5.symbol_info_tick(s["broker"])
            if tick is None:
                continue
            spread = tick.ask - tick.bid
            if row.direction > 0:
                best = max([float(x["high"]) for x in (m15 if m15 is not None else [])] + [tick.bid, row.entry_price])
            else:
                best = min([float(x["low"]) + spread for x in (m15 if m15 is not None else [])] + [tick.ask, row.entry_price])
            risk = abs(row.entry_price - row.initial_stop)
            new = S.manage_stop(row.direction, row.entry_price, pos.sl or row.current_stop, risk, best, row.atr_at_entry,
                                trail=(rules == "pullback"))
            if new is None:
                continue
            new = round(new, s["digits"])
            min_gap = (s["stops"] or 0) * s["point"] + spread
            cur = tick.bid if row.direction > 0 else tick.ask
            if (cur - new) * row.direction <= min_gap:
                continue                      # too close to price for the broker; try next loop
            if pos.sl and (new - pos.sl) * row.direction < 2 * s["point"]:
                continue
            if self.modify_stop(pos, new):
                row.current_stop = new
                self._commit()
                what = "breakeven" if abs(new - row.entry_price) < 1e-9 else "trailing stop"
                self.event("exit", f"{canon} {what} \u2192 {fmt(new, s['digits'])}", canon, "ok")

    def manage_paper(self, row: DeskTrade):
        """Walk the paper position through every M15 bar since entry, in order: a gap through the
        stop fills at the open, the stop is checked before the target, breakeven applies from the
        next bar. Long exits at bid, short exits at ask (bid + the bar's spread). Then the live tick."""
        s = self.sym[row.symbol]
        st = self.paper.get(row.ticket)
        opened = row.opened_at if row.opened_at.tzinfo else row.opened_at.replace(tzinfo=timezone.utc)
        if st is None:   # (re)start: rebuild from the entry bar, so the result never depends on restarts
            first_bar = int(opened.timestamp()) // 900 * 900
            st = self.paper[row.ticket] = {"seen": first_bar, "sl": row.initial_stop, "best": row.entry_price}
        d, entry, tp = row.direction, row.entry_price, row.target
        risk = abs(entry - row.initial_stop)
        rules = self.strategy_of(row.symbol)
        now = now_utc().timestamp()
        rates = mt5.copy_rates_range(s["broker"], mt5.TIMEFRAME_M15,
                                     datetime.fromtimestamp(st["seen"] + self.offset_sec, timezone.utc),
                                     datetime.fromtimestamp(now + self.offset_sec + 900, timezone.utc))
        exit_px = reason = None
        exit_t = None
        for x in (rates if rates is not None else []):
            t = int(x["time"]) - self.offset_sec
            if t < st["seen"] or t + 900 > now:
                continue                      # already processed, or still forming
            o, h, l, c = float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"])
            sp = float(x["spread"]) * s["point"]
            lo, hi, op = (l, h, o) if d > 0 else (l + sp, h + sp, o + sp)
            sl = st["sl"]
            if (d > 0 and op <= sl) or (d < 0 and op >= sl):
                exit_px, reason = op, "stop"
            elif (d > 0 and lo <= sl) or (d < 0 and hi >= sl):
                exit_px = sl
                reason = "breakeven" if abs(sl - entry) < 1e-12 else ("trail" if (sl - entry) * d > 0 else "stop")
            elif (d > 0 and hi >= tp) or (d < 0 and lo <= tp):
                exit_px, reason = tp, "target"
            st["seen"] = t + 900
            if exit_px is not None:
                exit_t = t + 900
                break
            st["best"] = max(st["best"], h) if d > 0 else min(st["best"], l + sp)
            new = S.manage_stop(d, entry, st["sl"], risk, st["best"], row.atr_at_entry, trail=(rules == "pullback"))
            if new is not None and (new - st["sl"]) * d > 0:
                st["sl"] = new
                row.current_stop = new
                self._commit()
                what = "breakeven" if abs(new - entry) < 1e-12 else "trailing stop"
                self.event("exit", f"PAPER {row.symbol} {what} \u2192 {fmt(new, s['digits'])}", row.symbol, "ok",
                           {"paper": True, "ticket": row.ticket})
        if exit_px is None:                   # live tick inside the forming bar
            tick = mt5.symbol_info_tick(s["broker"])
            if tick is not None and self.market_open(s["broker"], tick):
                px = tick.bid if d > 0 else tick.ask
                sl = st["sl"]
                if (px - sl) * d <= 0:        # through the stop: a real stop fills at market, not at the level
                    exit_px = px
                    reason = "breakeven" if abs(sl - entry) < 1e-12 else ("trail" if (sl - entry) * d > 0 else "stop")
                elif (px - tp) * d >= 0:
                    exit_px, reason = tp, "target"
                elif rules == "pullback" and self.bias.get(row.symbol, 0) == -d:
                    exit_px, reason = px, "bias_flip"
                exit_t = now
        if exit_px is None:
            return
        otype = mt5.ORDER_TYPE_BUY if d > 0 else mt5.ORDER_TYPE_SELL
        profit = mt5.order_calc_profit(otype, s["broker"], row.lot, entry, exit_px) or 0.0
        row.is_open = False
        row.closed_at = datetime.fromtimestamp(exit_t, timezone.utc)
        row.exit_price = exit_px
        row.profit_usd = round(profit, 2)
        row.r_multiple = round((exit_px - entry) * d / risk, 2) if risk else None
        row.exit_reason = reason
        self._commit()
        self.paper.pop(row.ticket, None)
        self.event("exit", f"PAPER {row.symbol} closed \u00b7 {reason} \u00b7 "
                           f"{'+' if profit >= 0 else '-'}${fmt(abs(profit))} ({row.r_multiple:+.2f}R) "
                           f"@ {fmt(exit_px, s['digits'])}",
                   row.symbol, "ok" if profit >= 0 else "warn",
                   {"paper": True, "ticket": row.ticket, "profit": row.profit_usd, "r": row.r_multiple, "reason": reason})

    def record_close(self, row: DeskTrade):
        deals = mt5.history_deals_get(position=int(row.ticket)) or []
        out = [x for x in deals if x.entry in (getattr(mt5, "DEAL_ENTRY_OUT", 1), getattr(mt5, "DEAL_ENTRY_OUT_BY", 3))]
        if not deals:
            # history not synced yet; ask for the last week and retry next loop
            mt5.history_select(now_utc() - timedelta(days=7), now_utc() + timedelta(days=1))
            if (now_utc() - (row.opened_at if row.opened_at.tzinfo else row.opened_at.replace(tzinfo=timezone.utc))) < timedelta(minutes=10):
                return
        profit = sum(x.profit + x.swap + x.commission for x in deals)
        exit_px = out[-1].price if out else None
        reason = self.closing_reason.pop(row.ticket, None)
        if not reason and out:
            r = out[-1].reason
            if r == getattr(mt5, "DEAL_REASON_TP", 5):
                reason = "target"
            elif r == getattr(mt5, "DEAL_REASON_SL", 4):
                if abs(row.current_stop - row.entry_price) < 1e-9:
                    reason = "breakeven"
                elif (row.current_stop - row.entry_price) * row.direction > 0:
                    reason = "trail"
                else:
                    reason = "stop"
            elif r == getattr(mt5, "DEAL_REASON_SO", 6):
                reason = "stop_out"
            else:
                reason = "manual"
        row.is_open = False
        row.closed_at = now_utc()
        row.exit_price = exit_px
        row.profit_usd = round(profit, 2)
        row.r_multiple = round(profit / row.risk_usd, 2) if row.risk_usd else None
        row.exit_reason = reason or "unknown"
        self._commit()
        digits = self.sym.get(row.symbol, {}).get("digits", 2)
        self.event("exit", f"{row.symbol} closed \u00b7 {row.exit_reason} \u00b7 "
                           f"{'+' if profit >= 0 else '-'}${fmt(abs(profit))}"
                           + (f" ({row.r_multiple:+.2f}R)" if row.r_multiple is not None else "")
                           + (f" @ {fmt(exit_px, digits)}" if exit_px else ""),
                   row.symbol, "ok" if profit >= 0 else "warn",
                   {"ticket": row.ticket, "profit": row.profit_usd, "r": row.r_multiple, "reason": row.exit_reason})

    def adopt_orphans(self):
        """A position with our magic but no desk_trades row (e.g. DB was down at entry)."""
        known = {r.ticket for r in self.db.query(DeskTrade.ticket).filter(
            DeskTrade.worker == self.cfg.WORKER, DeskTrade.is_open.is_(True)).all()}
        for p in self.our_positions():
            if str(p.ticket) in known:
                continue
            canon = next((c for c, v in self.sym.items() if v["broker"] == p.symbol), p.symbol)
            d = 1 if p.type == mt5.POSITION_TYPE_BUY else -1
            dec = self.decision.get(canon)
            atr_v = dec.atr if dec and dec.atr else abs(p.price_open - (p.sl or p.price_open)) / S.STOP_ATR or 1.0
            sl = p.sl or p.price_open - d * S.STOP_ATR * atr_v
            loss1 = mt5.order_calc_profit(p.type, p.symbol, p.volume, p.price_open, sl)
            self.db.add(DeskTrade(worker=self.cfg.WORKER, ticket=str(p.ticket), symbol=canon, broker_symbol=p.symbol,
                                  direction=d, lot=p.volume, entry_price=p.price_open, initial_stop=sl,
                                  current_stop=sl, target=p.tp or 0.0, atr_at_entry=atr_v,
                                  risk_usd=abs(loss1) if loss1 else None,
                                  opened_at=datetime.fromtimestamp(p.time - self.offset_sec, timezone.utc)))
            self._commit()
            self.event("chief", f"adopted open {canon} position #{p.ticket} after restart", canon, "warn")

    # ---------------------------------------------------------------- state ---
    def write_state(self, info):
        positions = []
        for p in self.our_positions():
            canon = next((c for c, v in self.sym.items() if v["broker"] == p.symbol), p.symbol)
            positions.append({"ticket": str(p.ticket), "symbol": canon, "direction": 1 if p.type == mt5.POSITION_TYPE_BUY else -1,
                              "lot": p.volume, "entry": p.price_open, "sl": p.sl, "tp": p.tp,
                              "price": p.price_current, "profit": round(p.profit + p.swap, 2)})
        for row in (self.db.query(DeskTrade).filter(DeskTrade.worker == self.cfg.WORKER, DeskTrade.is_open.is_(True),
                                                    DeskTrade.ticket.like("paper-%")).all()):
            s = self.sym.get(row.symbol)
            tick = mt5.symbol_info_tick(s["broker"]) if s else None
            px = (tick.bid if row.direction > 0 else tick.ask) if tick else None
            otype = mt5.ORDER_TYPE_BUY if row.direction > 0 else mt5.ORDER_TYPE_SELL
            pr = mt5.order_calc_profit(otype, s["broker"], row.lot, row.entry_price, px) if (s and px) else None
            positions.append({"ticket": row.ticket, "symbol": row.symbol, "direction": row.direction, "lot": row.lot,
                              "entry": row.entry_price, "sl": row.current_stop, "tp": row.target, "price": px,
                              "profit": round(pr, 2) if pr is not None else 0.0, "paper": True})
        symbols = {}
        for canon, s in self.sym.items():
            tick = mt5.symbol_info_tick(s["broker"])
            m15 = self.bars(s["broker"], mt5.TIMEFRAME_M15, 0, self.cfg.M15_CANDLES)
            d = self.decision.get(canon)
            symbols[canon] = {
                "broker_symbol": s["broker"], "digits": s["digits"],
                "bid": getattr(tick, "bid", None), "ask": getattr(tick, "ask", None),
                "open": self.market_open(s["broker"], tick),
                "bias": self.bias.get(canon, 0),
                "atr": round(d.atr, 4) if d else None, "ema20": round(d.ema20, s["digits"]) if d else None,
                "scout": d.scout if d else None, "checks": d.checks if d else None,
                "strategy": self.strategy_of(canon), "rules": S.RULES_TEXT[self.strategy_of(canon)],
                "shadow": self.shadow(canon),
                "candles": [[b["t"], b["o"], b["h"], b["l"], b["c"]] for b in m15],
            }
        entries, losses = self.day_counts() if self.day else (0, 0)
        payload = {
            "worker": self.cfg.WORKER, "updated": now_utc().isoformat(),
            "account": {"login": mask_login(info.login), "server": info.server, "currency": info.currency,
                        "demo": self.is_demo(info), "leverage": info.leverage,
                        "balance": info.balance, "equity": info.equity, "margin": info.margin,
                        "margin_free": info.margin_free},
            "mode": {"trading_enabled": self.cfg.TRADING_ENABLED,
                     "shadow": not self.cfg.TRADING_ENABLED,
                     "risk_pct": self.cfg.RISK_PCT, "daily_loss_pct": self.cfg.DAILY_LOSS_PCT,
                     "symbols": self.cfg.SYMBOLS, "shadow_symbols": sorted(self.cfg.SHADOW_SYMBOLS), "rules": S.RULES_TEXT["pullback"],
                     "strategies": {c: self.strategy_of(c) for c in self.cfg.SYMBOLS}},
            "day": {"date": str(self.day), "start_balance": self.day_start_balance,
                    "entries": entries, "losses": losses,
                    "daily_loss_limit_pct": self.cfg.DAILY_LOSS_PCT},
            "agents": self.agents, "positions": positions, "symbols": symbols,
            "broker_utc_offset_h": self.offset_sec // 3600,
        }
        try:
            row = self.db.query(DeskState).filter(DeskState.worker == self.cfg.WORKER).first()
            if not row:
                row = DeskState(worker=self.cfg.WORKER, payload=payload)
                self.db.add(row)
            else:
                row.payload = payload
                row.updated_at = now_utc()
            self._commit()
        except Exception as e:
            log.warning("state write failed: %s", e)
            self._commit()

    def heartbeat(self, detail: str):
        if WorkerHeartbeat is None or time.time() - self.last_beat < 30:
            return
        self.last_beat = time.time()
        try:
            row = self.db.query(WorkerHeartbeat).filter_by(worker_name=self.cfg.WORKER).first()
            if not row:
                row = WorkerHeartbeat(worker_name=self.cfg.WORKER)
                self.db.add(row)
            row.detail = detail[:200]
            row.last_beat = now_utc()
            self._commit()
        except Exception:
            self._commit()

    def prune(self):
        if time.time() - self.last_prune < 6 * 3600:
            return
        self.last_prune = time.time()
        try:
            cutoff = now_utc() - timedelta(days=self.cfg.EVENT_KEEP_DAYS)
            n = self.db.query(DeskEvent).filter(DeskEvent.worker == self.cfg.WORKER,
                                                DeskEvent.created_at < cutoff).delete()
            self._commit()
            if n:
                log.info("pruned %d old events", n)
        except Exception:
            self._commit()

    # ----------------------------------------------------------------- loop ---
    def step(self, first: bool = False):
        if not self.ensure_connected():
            return
        info = mt5.account_info()
        self.roll_day(info)
        if not self.is_demo(info) and not self.cfg.ALLOW_REAL:
            self.warn_once("real", "guard", "REAL account detected \u00b7 no new trades (DESK_ALLOW_REAL is off)", level="error")

        ticks = {}
        for canon in self.cfg.SYMBOLS:
            s = self.resolve(canon)
            if s:
                ticks[canon] = mt5.symbol_info_tick(s["broker"])
        self.learn_offset(ticks)

        for canon in self.cfg.SYMBOLS:
            s = self.resolve(canon)
            if not s:
                self.warn_once(f"nosym-{canon}", "check", f"{canon} is not offered by this broker", canon, "error")
                continue
            self.manage(canon)                    # close what has closed before deciding on new signals
            h1 = self.bars(s["broker"], mt5.TIMEFRAME_H1, 1, self.cfg.H1_BARS)
            if len(h1) < 3:
                continue
            last_t = h1[-1]["t"]
            if self.last_h1.get(canon) != last_t:
                h4 = self.bars(s["broker"], mt5.TIMEFRAME_H4, 1, self.cfg.H4_BARS)
                closed_age = now_utc().timestamp() - (last_t + 3600)
                act = (not first) and closed_age <= self.cfg.LATE_SIGNAL_SEC and self.offset_known
                self.last_h1[canon] = last_t
                self.on_h1_close(canon, info, h1, h4, act)
            elif canon not in self.bias:
                self.bias[canon] = S.h4_bias(self.bars(s["broker"], mt5.TIMEFRAME_H4, 1, self.cfg.H4_BARS))
            self.manage(canon)

        self.adopt_orphans()
        info = mt5.account_info() or info
        self.write_state(info)
        self.heartbeat(f"bal={info.balance:.2f} eq={info.equity:.2f} pos={len(self.our_positions())}")
        self.prune()
        self.touch()

    def touch(self):
        """Tell the launcher we are alive (it restarts a Desk that stops touching this file)."""
        if self.cfg.HEARTBEAT_FILE:
            try:
                with open(self.cfg.HEARTBEAT_FILE, "w") as f:
                    f.write(now_utc().isoformat())
            except OSError:
                pass

    def run(self):
        mode = "LIVE ORDERS" if self.cfg.TRADING_ENABLED else "SHADOW (no orders)"
        if self.cfg.TRADING_ENABLED and self.cfg.SHADOW_SYMBOLS:
            mode += " \u00b7 shadow only: " + ",".join(sorted(self.cfg.SHADOW_SYMBOLS))
        log.info("Desk worker %s starting \u00b7 symbols=%s \u00b7 risk=%.2f%% \u00b7 %s",
                 self.cfg.WORKER, ",".join(self.cfg.SYMBOLS), self.cfg.RISK_PCT, mode)
        while not self.connect():
            log.error("retrying MT5 connect in 30s")
            self.touch()
            sleep(30)
        rules = ", ".join(f"{c} {self.strategy_of(c)}{' (paper)' if self.shadow(c) else ''}" for c in self.cfg.SYMBOLS)
        self.event("chief", f"Desk online \u00b7 {mode} \u00b7 {rules} \u00b7 risk {self.cfg.RISK_PCT}%",
                   level="ok" if self.cfg.TRADING_ENABLED else "warn")
        first = True
        while True:
            t0 = time.time()
            try:
                self.step(first)
                first = False
            except Exception as e:
                log.exception("step failed: %s", e)
                try:
                    self.db.rollback()
                except Exception:
                    pass
            sleep(max(0.5, self.cfg.LOOP_SEC - (time.time() - t0)))


_LOCK = None


def single_instance(port: int) -> bool:
    """Hold a localhost port for the life of the process. Two Desks on one account would double every
    order, so a second copy (a double-clicked run_desk.bat next to the scheduled one) refuses to start."""
    global _LOCK
    if not port:
        return True
    _LOCK = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _LOCK.bind(("127.0.0.1", port))
        _LOCK.listen(1)
        return True
    except OSError:
        return False


def main():
    global mt5
    if not single_instance(Cfg.LOCK_PORT):
        log.critical("another Desk worker is already running on this machine - this copy is exiting")
        sys.exit(3)
    import MetaTrader5 as _mt5
    mt5 = _mt5
    Desk().run()


if __name__ == "__main__":
    main()
