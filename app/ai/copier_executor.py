"""
================================================================================
  COPIER EXECUTOR  —  master-account copy trading, direct MT5
================================================================================

  Called from the copier fast lane at the point where the terminal is ALREADY
  logged into the client's account.

      from app.ai.copier_executor import process_copier_executions
      copier_result = process_copier_executions(db, account, symbol_resolver)

  ── PROTECTIONS CARRIED OVER FROM THE METAAPI VERSION ───────────────────────
      • duplicate prevention — one master ticket opens once, ever
      • manual-close respect — if the CLIENT closed it, never reopen it
      • stale-open skip      — an open event past the age limit is not chased
      • per-row status       — executed / skipped / failed + error text

  ── REVISION HISTORY ────────────────────────────────────────────────────────
  10 Aug (a) — symbol_select fix, is_open column fix, lost-ticket recovery.
  10 Aug (b) — broker-agnostic symbol resolution, risk-mode fan-out,
               copier-only position counting, partial-fill reporting, lot
               normalisation.
  31 Aug (c) — THIS REVISION: symbol matcher rewritten for admin-added symbols.

  1. COLLIDING SYNTHETICS FIXED. The old matcher truncated any long name to its
     first 6 letters, so "Volatility 75 Index", "Volatility 25 Index" and
     "Volatility 100 Index" ALL reduced to "VOLATI". A master trade on
     Volatility 75 would have matched a client's Volatility 25 setting and
     opened the WRONG INSTRUMENT. Anything containing a digit now keeps its
     full identity, because in synthetics the number IS the instrument.

  2. "GOLD BASKET" NO LONGER BECOMES "GOLD". The letter-trimming loop chewed
     "GOLDBASKET" down to "GOLD" and matched it to XAUUSD, so a gold trade
     would have fanned out to anyone holding the Gold Basket index. Trimming is
     now applied only to single-token names (XAUUSDc, GOLDmicro, EURUSDz); a
     name containing a space is descriptive, not decorated, and is kept whole.

  3. Full Deriv synthetic coverage: Volatility 10-100 and all nine (1s)
     variants, Jump 10-100, Boom and Crash 300/500/1000, Range Break 100/200,
     Step / Step 200 / Step 500 / Multi Step, Drift Switch 10/20/30, DEX
     600/900/1500 UP and DOWN, and the five baskets.

  KEEP THE MATCHER BLOCK IDENTICAL to the one in app/routers/copier.py. If they
  disagree, the router creates rows this executor silently skips as "not
  enabled by client".
================================================================================
"""

import os
import time
import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

import MetaTrader5 as mt5
from sqlalchemy.orm import Session

from app.models import (
    ClientSymbolSetting,
    License,
    TradeExecution,
    TradeTicketMap,
)

logger = logging.getLogger("trader")


# ==============================================================================
# CONFIG
# ==============================================================================
# An open event older than this is not chased — the price has moved on and
# entering late is worse than not entering.
#
# WATCH THIS NUMBER. A full fast-lane cycle takes ~90-180s depending on the
# lane. With the limit at 300s there is limited headroom, so licences at the
# tail start getting skipped as stale as the list grows.
MAX_OPEN_EVENT_AGE_SEC = int(os.environ.get("COPIER_MAX_OPEN_AGE_SEC", "60"))

COPIER_MAGIC = int(os.environ.get("COPIER_MAGIC", "77001"))
MAX_ROWS_PER_ACCOUNT = int(os.environ.get("COPIER_MAX_ROWS", "10"))
COPIER_ENABLED = os.environ.get("COPIER_ENABLED", "true").lower() == "true"
_DEVIATION = int(os.environ.get("COPIER_DEVIATION", "30"))
_SYMBOL_TICK_WAIT_SEC = float(os.environ.get("COPIER_SYMBOL_WAIT", "2.0"))

# Risk mode: how many trades the client gets from ONE master signal, and the
# ceiling on copier positions open per symbol at once.
RISK_CAPS = {"normal": 1, "medium": 3, "aggressive": 5}
DEFAULT_RISK = "medium"


def _risk_cap(account) -> int:
    level = (getattr(account, "risk_level", None) or DEFAULT_RISK).strip().lower()
    return RISK_CAPS.get(level, RISK_CAPS[DEFAULT_RISK])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt) -> Optional[datetime]:
    """Postgres columns come back naive in some paths; normalise before diffing."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ==============================================================================
# SYMBOL MATCHING  —  any instrument, any broker naming
# ==============================================================================
# MUST STAY IDENTICAL to the block in app/routers/copier.py.
#
# Brokers decorate the same instrument in wildly different ways:
#     XAUUSD  XAUUSDc  XAUUSDm  XAUUSD.raw  XAUUSD-ECN  GOLD  GOLDmicro
#     Volatility 75 Index  V75  VIX75  Volatility75Index
#
# HOW A SYMBOL IS MATCHED:
#   1. strip separator decoration (. _ - # /) and collapse spaces
#   2. exact synonym hit wins
#   3. contains a digit, or the original had a space  -> keep whole
#   4. otherwise treat as an FX-style token: trim trailing broker letters,
#      checking synonyms at every length, then fall back to first 6 letters
#
# Rules 3 exists because of two real collisions: three Volatility indices all
# reducing to "VOLATI", and "Gold Basket" reducing to "GOLD".

_DERIV_VOL = (10, 25, 50, 75, 100)
_DERIV_VOL_1S = (10, 25, 50, 75, 100, 150, 200, 250, 300)
_DERIV_JUMP = (10, 25, 50, 75, 100)
_DERIV_BOOM_CRASH = (300, 500, 1000)

_NOISE_SUFFIX = {
    "CASH", "SPOT", "IDX", "INDEX", "RAW", "ECN", "PRO", "STD", "STP", "MICRO",
    "MINI", "M", "C", "Z", "E", "R", "X", "FT", "FUT", "ROLL", "RFD", "SB",
}

_SYNONYM_GROUPS = [
    {"XAUUSD", "GOLD", "GOLDUSD", "GOLDSPOT"},
    {"XAGUSD", "SILVER", "SILVERUSD"},
    {"XCUUSD", "COPPER"},
    {"XPTUSD", "PLATINUM"},
    {"BTCUSD", "BTCUSDT", "BITCOIN"},
    {"ETHUSD", "ETHUSDT", "ETHEREUM"},
    {"US30", "DJ30", "DOW", "DOW30", "WS30", "USA30", "DJI30", "YM", "US30CASH"},
    {"NAS100", "US100", "USTEC", "NDX100", "USA100", "TECH100", "NQ100", "NQ", "USTECH100"},
    {"SPX500", "US500", "SP500", "USA500", "ES", "SPX"},
    {"USOIL", "WTI", "CRUDE", "XTIUSD", "USOUSD", "WTIUSD", "CL", "CRUDEOIL"},
    {"UKOIL", "BRENT", "XBRUSD", "UKOUSD", "BRENTUSD"},
    {"NATGAS", "NGAS", "XNGUSD", "NATURALGAS"},
    {"GER40", "DE40", "DAX40", "GER30", "DE30", "DAX", "GERMANY40", "GERMANY30"},
    {"UK100", "FTSE100", "FTSE", "GB100", "BRITAIN100"},
    {"JP225", "JPN225", "NIKKEI", "N225", "JAPAN225"},
    {"FRA40", "CAC40", "FR40", "FRANCE40"},
    {"AUS200", "AU200", "ASX200", "AUSTRALIA200"},
    {"HK50", "HKG33", "HSI", "HONGKONG50"},
    {"EU50", "STOXX50", "EUSTX50", "ESX50", "EUROPE50"},
    {"US2000", "RUSSELL2000", "RUT", "USA2000"},
    {"STEPINDEX", "STEP"},
    {"MULTISTEPINDEX", "MULTISTEP"},
]

for _n in _DERIV_VOL:
    _SYNONYM_GROUPS.append({f"VOLATILITY{_n}INDEX", f"VOLATILITY{_n}", f"V{_n}", f"VIX{_n}", f"VOL{_n}"})
for _n in _DERIV_VOL_1S:
    _SYNONYM_GROUPS.append({f"VOLATILITY{_n}(1S)INDEX", f"VOLATILITY{_n}(1S)", f"V{_n}(1S)", f"VOL{_n}(1S)"})
for _n in _DERIV_JUMP:
    _SYNONYM_GROUPS.append({f"JUMP{_n}INDEX", f"JUMP{_n}", f"J{_n}"})
for _n in _DERIV_BOOM_CRASH:
    _SYNONYM_GROUPS.append({f"BOOM{_n}INDEX", f"BOOM{_n}", f"B{_n}"})
    _SYNONYM_GROUPS.append({f"CRASH{_n}INDEX", f"CRASH{_n}", f"C{_n}"})
for _n in (100, 200):
    _SYNONYM_GROUPS.append({f"RANGEBREAK{_n}INDEX", f"RANGEBREAK{_n}", f"RB{_n}"})
for _n in (200, 500):
    _SYNONYM_GROUPS.append({f"STEP{_n}INDEX", f"STEP{_n}"})
for _n in (10, 20, 30):
    _SYNONYM_GROUPS.append({f"DRIFTSWITCHINDEX{_n}", f"DRIFTSWITCH{_n}", f"DSI{_n}"})
for _n in (600, 900, 1500):
    for _d in ("UP", "DOWN"):
        _SYNONYM_GROUPS.append({f"DEX{_n}{_d}INDEX", f"DEX{_n}{_d}"})
for _b in ("AUD", "EUR", "GBP", "USD", "GOLD"):
    _SYNONYM_GROUPS.append({f"{_b}BASKET", f"{_b}BASKETINDEX"})

_SYNONYM_LOOKUP: Dict[str, str] = {}
for _group in _SYNONYM_GROUPS:
    _canon_name = sorted(_group)[0]
    for _name in _group:
        _SYNONYM_LOOKUP[_name] = _canon_name


def _strip_decoration(sym: str) -> str:
    s = (sym or "").upper().strip()
    s = s.lstrip(".#_-/ ")
    parts, buf = [], ""
    for ch in s:
        if ch in "._-#/ ":
            if buf:
                parts.append(buf); buf = ""
        else:
            buf += ch
    if buf:
        parts.append(buf)
    if not parts:
        return ""
    while len(parts) > 1 and parts[-1] in _NOISE_SUFFIX:
        parts.pop()
    return "".join(parts)


def _canonical(sym: str) -> str:
    raw = (sym or "").upper().strip()
    s = _strip_decoration(raw)
    if not s:
        return ""
    if s in _SYNONYM_LOOKUP:
        return _SYNONYM_LOOKUP[s]

    if any(ch.isdigit() for ch in s):
        tail = ""
        while s and s[-1].isalpha():
            tail = s[-1] + tail
            s = s[:-1]
            if s in _SYNONYM_LOOKUP:
                return _SYNONYM_LOOKUP[s]
            if tail in _NOISE_SUFFIX and s and s[-1].isdigit():
                break
        return _strip_decoration(raw)

    if " " in raw:
        return s

    fx_guess = None
    for cut in range(1, 7):
        if len(s) - cut < 3:
            break
        cand = s[:len(s) - cut]
        if cand in _SYNONYM_LOOKUP:
            return _SYNONYM_LOOKUP[cand]
        if fx_guess is None and len(cand) == 6 and cand.isalpha():
            fx_guess = cand
    if fx_guess:
        return fx_guess
    if len(s) > 6:
        return s[:6]
    return s


# Cache the resolution per logged-in account. mt5.symbols_get() can return
# thousands of rows and is far too expensive to call per trade row.
_SYMBOL_CACHE: Dict[str, object] = {"login": None, "map": {}}


def _resolve_symbol(master_symbol: str, login, fallback: Optional[str]) -> Optional[str]:
    """Find the name THIS terminal uses for the master's symbol.

    Returns None if the broker genuinely does not offer the instrument, which
    is a legitimate skip rather than a failure. Note that Deriv synthetics only
    exist on Deriv, so a Volatility 75 signal will correctly skip every client
    on Exness, FBS and the rest.
    """
    if not master_symbol:
        return None

    if _SYMBOL_CACHE.get("login") != login:
        _SYMBOL_CACHE["login"] = login
        _SYMBOL_CACHE["map"] = {}

    key = master_symbol.upper()
    cached = _SYMBOL_CACHE["map"].get(key)
    if cached is not None:
        return cached or None      # "" is a cached negative

    want = _canonical(master_symbol)

    try:
        all_syms = mt5.symbols_get()
    except Exception as e:
        logger.warning("symbols_get failed: %s", e)
        all_syms = None

    best_name, best_score = None, -1
    for s in (all_syms or []):
        name = getattr(s, "name", "") or ""
        if _canonical(name) != want:
            continue

        # Prefer an exact name match, then a symbol already in Market Watch,
        # then the shortest name (plain XAUUSD over XAUUSD.pro_ecn_2).
        score = 0
        if name.upper() == key:
            score += 10
        if getattr(s, "visible", False):
            score += 3
        score += max(0, 12 - len(name))
        if score > best_score:
            best_name, best_score = name, score

    # Fall back to the worker's own resolver, but only if THIS terminal
    # actually knows that name — that check is what the old code was missing.
    if not best_name and fallback:
        try:
            if mt5.symbol_info(fallback) is not None:
                best_name = fallback
        except Exception:
            pass

    _SYMBOL_CACHE["map"][key] = best_name or ""
    if best_name and best_name.upper() != key:
        logger.info("symbol %s → %s on login %s", master_symbol, best_name, login)
    return best_name


# ==============================================================================
# MT5 PRIMITIVES
# ==============================================================================
def _ensure_symbol(broker_symbol: str,
                   wait_sec: float = _SYMBOL_TICK_WAIT_SEC) -> Tuple[bool, str]:
    """Make the symbol tradable in THIS terminal, then wait for a live tick.

    symbol_info_tick() returns nothing for a symbol that is not in the client's
    Market Watch, and most clients have never manually opened XAUUSDc or
    BTCUSDm. Selecting it first is what makes suffixed broker symbols work.
    """
    if not broker_symbol:
        return False, "no broker symbol"

    try:
        info = mt5.symbol_info(broker_symbol)
    except Exception as e:
        return False, f"symbol_info raised for {broker_symbol}: {e}"

    if info is None:
        try:
            mt5.symbol_select(broker_symbol, True)
            info = mt5.symbol_info(broker_symbol)
        except Exception as e:
            return False, f"symbol_select raised for {broker_symbol}: {e}"
        if info is None:
            return False, f"unknown symbol {broker_symbol}"

    if not getattr(info, "visible", False):
        try:
            if not mt5.symbol_select(broker_symbol, True):
                return False, f"cannot select {broker_symbol}"
        except Exception as e:
            return False, f"symbol_select raised for {broker_symbol}: {e}"

    deadline = time.time() + max(0.0, wait_sec)
    while True:
        try:
            tick = mt5.symbol_info_tick(broker_symbol)
        except Exception:
            tick = None
        if tick and (getattr(tick, "ask", 0) or getattr(tick, "bid", 0)):
            return True, ""
        if time.time() >= deadline:
            break
        time.sleep(0.15)

    return False, f"no tick for {broker_symbol} — market may be closed"


def _positions_for_symbol(broker_symbol: str) -> List:
    try:
        rows = mt5.positions_get(symbol=broker_symbol)
        return list(rows) if rows else []
    except Exception:
        return []


def _copier_positions(broker_symbol: str) -> List:
    """Only positions this copier opened. The client's own manual trades and
    their AI trades are not ours to police."""
    return [p for p in _positions_for_symbol(broker_symbol)
            if getattr(p, "magic", 0) == COPIER_MAGIC]


def _position_by_ticket(ticket) -> Optional[object]:
    """How we tell 'client closed it themselves' apart from 'never opened'."""
    try:
        rows = mt5.positions_get(ticket=int(ticket))
        return rows[0] if rows else None
    except Exception:
        return None


def _newest_copier_ticket(broker_symbol: str) -> Optional[int]:
    """Recover the ticket of a copier position we just opened.

    Some brokers return a filled order with order == 0 and deal == 0. Without
    this, a trade that went to market is written down as a failure and gets no
    ticket map, so it can never be closed by a later master close event.
    """
    newest = None
    for p in _copier_positions(broker_symbol):
        if newest is None or getattr(p, "time", 0) >= getattr(newest, "time", 0):
            newest = p
    try:
        return int(newest.ticket) if newest is not None else None
    except Exception:
        return None


def _normalise_lot(broker_symbol: str, lot: float) -> Tuple[float, str]:
    """Clamp the lot to what this broker accepts.

    Brokers differ on min/max/step — 0.01 is not universal, and an out-of-range
    volume is rejected outright with a retcode that looks like a code bug.
    """
    try:
        info = mt5.symbol_info(broker_symbol)
        if info is None:
            return lot, ""
        vmin = float(getattr(info, "volume_min", 0.01) or 0.01)
        vmax = float(getattr(info, "volume_max", 100.0) or 100.0)
        vstep = float(getattr(info, "volume_step", 0.01) or 0.01)

        adj = max(vmin, min(float(lot), vmax))
        if vstep > 0:
            adj = round(round(adj / vstep) * vstep, 8)
            adj = max(vmin, adj)

        note = "" if abs(adj - float(lot)) < 1e-9 else \
            f"lot {lot} adjusted to {adj} (broker min {vmin} step {vstep})"
        return adj, note
    except Exception:
        return lot, ""


def _filling_modes(broker_symbol: str) -> List[int]:
    """Brokers disagree about which filling mode they accept and reject the
    order outright if you guess wrong."""
    modes: List[int] = []
    try:
        info = mt5.symbol_info(broker_symbol)
        if info is not None:
            declared = getattr(info, "filling_mode", 0) or 0
            if declared & 1:
                modes.append(mt5.ORDER_FILLING_FOK)
            if declared & 2:
                modes.append(mt5.ORDER_FILLING_IOC)
    except Exception:
        pass
    for m in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        if m not in modes:
            modes.append(m)
    return modes


def _send_market_order(
    broker_symbol: str,
    action: str,
    lot: float,
    sl: Optional[float],
    tp: Optional[float],
    comment: str,
) -> Tuple[Optional[int], str]:
    """Returns (ticket, error_text). Ticket is None on failure."""
    ok, sym_err = _ensure_symbol(broker_symbol)
    if not ok:
        return None, sym_err

    tick = mt5.symbol_info_tick(broker_symbol)
    if not tick:
        return None, f"no tick for {broker_symbol}"

    is_buy = action == "buy"
    price = tick.ask if is_buy else tick.bid
    if not price:
        return None, f"no price for {broker_symbol}"

    lot, lot_note = _normalise_lot(broker_symbol, lot)
    if lot_note:
        logger.info("%s: %s", broker_symbol, lot_note)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": broker_symbol,
        "volume": float(lot),
        "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price": float(price),
        "deviation": _DEVIATION,
        "magic": COPIER_MAGIC,
        "comment": comment[:30],
        "type_time": mt5.ORDER_TIME_GTC,
    }
    if sl:
        request["sl"] = float(sl)
    if tp:
        request["tp"] = float(tp)

    last_err = "unknown"
    for mode in _filling_modes(broker_symbol):
        request["type_filling"] = mode
        try:
            result = mt5.order_send(request)
        except Exception as e:
            last_err = f"order_send raised: {e}"
            continue
        if result is None:
            last_err = f"order_send returned None: {mt5.last_error()}"
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            ticket = int(getattr(result, "order", 0) or 0) or \
                     int(getattr(result, "deal", 0) or 0)
            if not ticket:
                ticket = _newest_copier_ticket(broker_symbol) or 0
            if ticket:
                return int(ticket), ""
            return None, "order filled but broker returned no ticket"
        last_err = f"retcode={result.retcode} {getattr(result, 'comment', '')}"
        # Only a filling-mode rejection is worth retrying with another mode.
        if result.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            break

    return None, last_err


def _close_position(ticket: int) -> Tuple[bool, str]:
    pos = _position_by_ticket(ticket)
    if not pos:
        return False, "position not found"

    ok, sym_err = _ensure_symbol(pos.symbol)
    if not ok:
        return False, sym_err

    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        return False, f"no tick for {pos.symbol}"

    is_buy = pos.type == mt5.POSITION_TYPE_BUY
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": float(pos.volume),
        "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
        "position": int(ticket),
        "price": tick.bid if is_buy else tick.ask,
        "deviation": _DEVIATION,
        "magic": COPIER_MAGIC,
        "comment": "copier close",
        "type_time": mt5.ORDER_TIME_GTC,
    }

    last_err = "unknown"
    for mode in _filling_modes(pos.symbol):
        request["type_filling"] = mode
        try:
            result = mt5.order_send(request)
        except Exception as e:
            last_err = f"order_send raised: {e}"
            continue
        if result is None:
            last_err = f"order_send returned None: {mt5.last_error()}"
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            return True, ""
        last_err = f"retcode={result.retcode} {getattr(result, 'comment', '')}"
        if result.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            break

    return False, last_err


def _modify_position(ticket: int, sl: Optional[float], tp: Optional[float]) -> Tuple[bool, str]:
    pos = _position_by_ticket(ticket)
    if not pos:
        return False, "position not found"

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": pos.symbol,
        "position": int(ticket),
        "sl": float(sl) if sl else float(pos.sl or 0),
        "tp": float(tp) if tp else float(pos.tp or 0),
    }
    try:
        result = mt5.order_send(request)
    except Exception as e:
        return False, f"order_send raised: {e}"
    if result is None:
        return False, f"order_send returned None: {mt5.last_error()}"
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        return True, ""
    return False, f"retcode={result.retcode} {getattr(result, 'comment', '')}"


# ==============================================================================
# TICKET MAP HELPERS
# ==============================================================================
def _maps_for(db: Session, row: TradeExecution, open_only: bool = False) -> List[TradeTicketMap]:
    """Ticket maps for this master ticket on this licence.

    COLUMN NAMES MATTER HERE. The real table is `ticket_maps`:
        id, license_id, execution_id, master_ticket, client_ticket, symbol,
        is_closed, closed_by_client, closed_at, last_error, created_at
    There is no ea_id, no child_ticket_index, no is_open and no manually_closed.
    A separate legacy `trade_ticket_maps` table with those columns exists and
    holds 0 rows — do not query it.
    """
    q = db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == row.license_id,
        TradeTicketMap.master_ticket == row.master_ticket,
    )
    if open_only:
        q = q.filter(TradeTicketMap.is_closed == False)  # noqa: E712
    return q.order_by(TradeTicketMap.id.asc()).all()


def _upsert_map(db: Session, row: TradeExecution, client_ticket: str) -> None:
    """One row per client ticket. Without child_ticket_index, the client ticket
    itself is what distinguishes the children of a multi-trade fan-out."""
    existing = db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == row.license_id,
        TradeTicketMap.master_ticket == row.master_ticket,
        TradeTicketMap.client_ticket == str(client_ticket),
    ).first()

    if existing:
        existing.symbol = row.symbol
        existing.execution_id = row.id
        existing.is_closed = False
        existing.closed_by_client = False
        existing.closed_at = None
        return

    db.add(TradeTicketMap(
        license_id=row.license_id,
        execution_id=row.id,
        master_ticket=row.master_ticket,
        client_ticket=str(client_ticket),
        symbol=row.symbol,
        is_closed=False,
        closed_by_client=False,
    ))


def _close_maps(db: Session, row: TradeExecution, manually_closed: bool) -> int:
    maps = _maps_for(db, row, open_only=True)
    now = _utc_now()
    for m in maps:
        m.is_closed = True
        m.closed_by_client = manually_closed
        m.closed_at = now
    return len(maps)


def _client_closed_it_themselves(db: Session, row: TradeExecution) -> bool:
    """If the client manually closed a copied trade, a later event for the same
    master ticket must NOT reopen it — otherwise the platform fights the user's
    own decision, which is the fastest way to lose their trust."""
    maps = _maps_for(db, row)
    if not maps:
        return False
    if any(m.closed_by_client for m in maps):
        return True

    open_maps = [m for m in maps if not m.is_closed and m.client_ticket]
    if not open_maps:
        return False

    still_alive = [m for m in open_maps if _position_by_ticket(m.client_ticket)]
    if open_maps and not still_alive:
        _close_maps(db, row, manually_closed=True)
        logger.info("client closed master_ticket=%s themselves — will not reopen",
                    row.master_ticket)
        return True
    return False


# ==============================================================================
# EVENT HANDLERS
# ==============================================================================
def _handle_open(db: Session, row: TradeExecution, broker_symbol: str,
                 setting: ClientSymbolSetting, account) -> Tuple[str, Optional[str], str]:
    """Returns (status, client_ticket, error_message)."""
    created = _aware(row.created_at)
    if created:
        age = (_utc_now() - created).total_seconds()
        if age > MAX_OPEN_EVENT_AGE_SEC:
            return "skipped", None, (
                f"stale open event — {age:.0f}s old, limit {MAX_OPEN_EVENT_AGE_SEC}s")

    if _client_closed_it_themselves(db, row):
        return "skipped", None, "client closed this trade manually; not reopening"

    # Duplicate prevention: has this master ticket already produced a live
    # position on this account? (The column is is_closed — there is no is_open.)
    for m in _maps_for(db, row):
        if (not m.is_closed) and m.client_ticket and _position_by_ticket(m.client_ticket):
            return "skipped", None, "already open for this master ticket"

    # Map rows left over from a dead position are stale — clear them.
    if _maps_for(db, row, open_only=True):
        _close_maps(db, row, manually_closed=False)

    if not setting.enabled:
        return "skipped", None, f"{row.symbol} disabled by client"

    direction = (getattr(setting, "trade_direction", None) or "both").lower()
    if direction == "buy" and row.action == "sell":
        return "skipped", None, "client accepts BUY only"
    if direction == "sell" and row.action == "buy":
        return "skipped", None, "client accepts SELL only"

    # RISK MODE IS THE FAN-OUT. The client picks it per account and it means
    # how many trades they get from one signal: normal 1, medium 3,
    # aggressive 5. That same number is the ceiling on how many copier
    # positions may be open on the symbol at once.
    cap = _risk_cap(account)
    per_signal = cap
    max_open = cap

    current = len(_copier_positions(broker_symbol))
    slots = max_open - current
    if slots <= 0:
        return "skipped", None, (
            f"risk mode {(getattr(account, 'risk_level', None) or DEFAULT_RISK)} "
            f"allows {max_open} open on {row.symbol}, {current} already open")

    lot = float(row.lot_size or getattr(setting, "lot_size", 0.01) or 0.01)
    sl = float(row.sl) if row.sl and str(row.sl) not in ("0", "0.0", "") else None
    tp = float(row.tp) if row.tp and str(row.tp) not in ("0", "0.0", "") else None
    comment = (row.comment or "Nolimitz Copier")[:30]

    wanted = min(per_signal, slots)
    tickets: List[int] = []
    last_error = ""
    for _ in range(wanted):
        ticket, err = _send_market_order(broker_symbol, row.action, lot, sl, tp, comment)
        if ticket:
            tickets.append(ticket)
            _upsert_map(db, row, str(ticket))
        else:
            last_error = err
            break

    if not tickets:
        return "failed", None, last_error or "order rejected"

    # A partial fill must not look like a clean success.
    partial = "" if len(tickets) == wanted else \
        f"opened {len(tickets)} of {wanted} — {last_error}"

    logger.info("📋 copier OPEN %s %s x%d lot=%.2f → %s",
                row.action.upper(), broker_symbol, len(tickets), lot, tickets)
    return "executed", str(tickets[0]), partial


def _handle_close(db: Session, row: TradeExecution) -> Tuple[str, Optional[str], str]:
    maps = _maps_for(db, row, open_only=True)
    if not maps:
        return "skipped", None, "no open trades mapped to this master ticket"

    closed: List[str] = []
    errors: List[str] = []
    for m in maps:
        if not m.client_ticket:
            continue
        if not _position_by_ticket(m.client_ticket):
            continue  # already gone — handled by the map close below
        ok, err = _close_position(int(m.client_ticket))
        if ok:
            closed.append(str(m.client_ticket))
        else:
            errors.append(f"{m.client_ticket}: {err}")

    if closed:
        _close_maps(db, row, manually_closed=False)
        logger.info("📋 copier CLOSE %s → %s", row.symbol, closed)
        return "executed", ",".join(closed), ""

    if not any(_position_by_ticket(m.client_ticket) for m in maps if m.client_ticket):
        _close_maps(db, row, manually_closed=True)
        return "skipped", None, "already closed by client"

    return "failed", None, "; ".join(errors) or "close failed"


def _handle_modify(db: Session, row: TradeExecution) -> Tuple[str, Optional[str], str]:
    maps = _maps_for(db, row, open_only=True)
    if not maps:
        return "skipped", None, "no open trades mapped to this master ticket"

    sl = float(row.sl) if row.sl and str(row.sl) not in ("0", "0.0", "") else None
    tp = float(row.tp) if row.tp and str(row.tp) not in ("0", "0.0", "") else None
    if sl is None and tp is None:
        return "skipped", None, "modify event carried no SL or TP"

    done: List[str] = []
    errors: List[str] = []
    for m in maps:
        if not m.client_ticket or not _position_by_ticket(m.client_ticket):
            continue
        ok, err = _modify_position(int(m.client_ticket), sl, tp)
        if ok:
            done.append(str(m.client_ticket))
        else:
            errors.append(f"{m.client_ticket}: {err}")

    if done:
        logger.info("📋 copier MODIFY %s → %s", row.symbol, done)
        return "executed", ",".join(done), ""
    return "skipped", None, "; ".join(errors) or "nothing to modify"


# ==============================================================================
# CLIENT SYMBOL SETTING LOOKUP
# ==============================================================================
def _setting_for(db: Session, license_id, master_symbol: str) -> Optional[ClientSymbolSetting]:
    """Match the client's own symbol setting to the master symbol.

    Compared on the canonical key rather than a hardcoded alias list, so any
    symbol an admin adds works with no code change — a client whose setting row
    says GOLD still receives an XAUUSD signal, and one holding
    "Volatility 75 Index" matches a master trading V75.
    """
    want = _canonical(master_symbol)
    if not want:
        return None
    rows = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == license_id).all()
    for s in rows:
        if _canonical(getattr(s, "symbol_name", "")) == want:
            return s
    return None


# ==============================================================================
# ENTRY POINT
# ==============================================================================
def process_copier_executions(
    db: Session,
    account,
    symbol_resolver: Callable[[str], Optional[str]],
) -> Dict[str, int]:
    """Process this account's pending copier rows. The terminal must ALREADY be
    logged into this account.

    Returns a tally for the cycle log, e.g. {"executed": 2, "skipped": 1}
    """
    tally: Dict[str, int] = {}
    if not COPIER_ENABLED or not account.license_id:
        return tally

    rows = db.query(TradeExecution).filter(
        TradeExecution.license_id == account.license_id,
        TradeExecution.status == "pending",
    ).order_by(TradeExecution.id.asc()).limit(MAX_ROWS_PER_ACCOUNT).all()

    if not rows:
        return tally

    # Licence gate. A lapsed licence stops NEW copier entries but still allows
    # close and modify, so nobody is left holding a position they cannot exit.
    licence_ok = True
    try:
        lic = db.query(License).filter(License.id == account.license_id).first()
        if lic is not None:
            exp = _aware(getattr(lic, "expires_at", None))
            if not lic.is_active or (exp and exp < _utc_now()):
                licence_ok = False
    except Exception:
        pass

    login = getattr(account, "login", None)

    for row in rows:
        # Claim it immediately so a concurrent shard cannot double-execute.
        row.status = "processing"
        try:
            db.commit()
        except Exception:
            db.rollback()
            continue

        status, client_ticket, error = "failed", None, ""
        try:
            if row.event_type == "open" and not licence_ok:
                status, error = "skipped", "licence expired — entries paused"

            elif row.event_type == "open":
                # Ask the worker's resolver, then confirm against THIS
                # terminal's own symbol list. The terminal wins.
                try:
                    fallback = symbol_resolver(row.symbol) if symbol_resolver else None
                except Exception:
                    fallback = None

                broker_symbol = _resolve_symbol(row.symbol, login, fallback)

                if not broker_symbol:
                    status, error = "skipped", f"{row.symbol} not offered by this broker"
                else:
                    setting = _setting_for(db, row.license_id, row.symbol)
                    if not setting:
                        status, error = "skipped", f"{row.symbol} not enabled by client"
                    else:
                        status, client_ticket, error = _handle_open(
                            db, row, broker_symbol, setting, account)

            elif row.event_type == "close":
                status, client_ticket, error = _handle_close(db, row)

            elif row.event_type == "modify":
                status, client_ticket, error = _handle_modify(db, row)

            else:
                status, error = "failed", f"unknown event type {row.event_type}"

        except Exception as e:
            status, error = "failed", str(e)[:300]
            logger.warning("copier row %s raised: %s", row.id, e)

        row.status = status
        row.client_ticket = client_ticket
        row.error_message = error or None
        try:
            db.commit()
        except Exception:
            db.rollback()

        tally[status] = tally.get(status, 0) + 1

    return tally
