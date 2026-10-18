"""
================================================================================
  telegram_be_orders.py  --  is this Telegram message an order to move the
                             stop-loss to BREAK-EVEN?
================================================================================

  Pure Python. No database, no MT5, no eval -- text in, decision out.

  Recognised (the stop goes to each customer's OWN entry price):
      "MOVE YOUR SL TO B.E"            "Move SL to breakeven"
      "SL to entry"                    "Set SL at BE now"
      "Put stop loss at entry"         "Move stops to break even"
      "SL @ BE"                        "Breakeven now" / "Set breakeven"
  ...including inside a results post:
      "XAUUSD 100 PIPS IN PROFIT / MOVE YOUR SL TO B.E / KEEP HOLD"

  Refused:
      questions                 "Should we move SL to BE?"
      conditions                "Move SL to BE when price hits 4150",
                                "Once TP1 hits, SL to entry", "after TP1 ..."
      negations                 "Don't move SL to BE yet", "No need for BE"
      optional advice           "You can move SL to BE if you want"
      partial instructions      "Move SL to BE on half"
      a different level         "Move SL to TP1", "SL to 4150"  (not break-even)
      plain English "be"        "TP will be updated"  (BE only counts next to
                                SL / STOP / MOVE / SET ... TO)
================================================================================
"""

import re
from typing import Iterable, Optional

_CONDITION = {
    "IF", "WHEN", "WHENEVER", "ONCE", "AFTER", "REACH", "REACHES", "REACHING",
    "UNLESS", "WHETHER", "INCASE",
}
_NEGATION = {"DONT", "DO_NOT", "NOT", "NEVER", "NO", "AVOID", "WITHOUT", "CANT",
             "WONT", "SHOULDNT", "DIDNT"}
_OPTIONAL = {"CAN", "MAY", "MIGHT", "COULD", "OPTIONAL", "WISH", "WANT", "PREFER",
             "CHOICE", "WOULD"}
_PARTIAL = {"HALF", "PARTIAL", "PARTIALLY", "SOME", "PART"}
_BUY = {"BUY", "BUYS", "LONG", "LONGS"}
_SELL = {"SELL", "SELLS", "SHORT", "SHORTS"}

_MAX_WORDS = 80

# After normalisation: break-even -> BE, stop loss -> SL, entry price -> ENTRY.
# "BE" is also the English verb ("SL to be updated", "be careful"), so a BE
# followed by one of these words is never the level.
_NOT_LEVEL = (r"(?!\s+(?:UPDATED|ADJUSTED|CONFIRMED|ANNOUNCED|SHARED|SENT|GIVEN|"
              r"POSTED|PROVIDED|DECIDED|SET|MOVED|PLACED|REVIEWED|CHANGED|"
              r"CAREFUL|PATIENT|READY|WAITING|DONE|HAPPY|SAFE|AWARE|SURE|"
              r"ADDED|REMOVED|HIT|TRIGGERED|IN|AT|THE|A|AN|SMART|CALM)\b)")
_PATTERNS = [
    # move / set / put ... SL ... to/at BE|ENTRY
    re.compile(r"\b(?:MOVE|MOVING|SET|SETTING|PUT|PLACE|SHIFT|ADJUST|BRING|"
               r"CHANGE|UPDATE|TAKE|PUSH|SECURE)\b(?:\s+\w+){0,4}?\s+SL\b"
               r"(?:\s+\w+){0,2}?\s+(?:TO|AT|ON)\s+(?:BE\b" + _NOT_LEVEL + r"|ENTRY\b)"),
    # SL to/at BE|ENTRY   ("SL TO BE", "SL AT ENTRY", "SL = BE")
    re.compile(r"\bSL\s+(?:TO|AT|ON|EQUALS)\s+(?:BE\b" + _NOT_LEVEL + r"|ENTRY\b)"),
    # move/set to BE   ("move to BE", "set BE", "set breakeven")
    re.compile(r"\b(?:MOVE|SET|PUT|SECURE|ACTIVATE|ENABLE)\s+(?:\w+\s+){0,2}?"
               r"(?:TO\s+|AT\s+)?BE\b" + _NOT_LEVEL),
    # "BREAKEVEN NOW" / "BE NOW" as its own line
    re.compile(r"(?:^|\n)\s*(?:SL\s+)?BE\s+NOW\b"),
]


def _normalise(text: str) -> str:
    s = text.upper().replace("’", "'")
    s = re.sub(r"\bDON'?T\b", " DONT ", s)
    s = re.sub(r"\bDO\s+NOT\b", " DO_NOT ", s)
    s = re.sub(r"\bCAN'?T\b|\bCANNOT\b", " CANT ", s)
    s = re.sub(r"\bWON'?T\b", " WONT ", s)
    s = re.sub(r"\bB\s*[./\\-]\s*E\b\.?", " BE ", s)          # B.E  B/E  B-E
    s = re.sub(r"\bBREAK\s*[- ]?\s*EVEN\b", " BE ", s)        # break even, break-even, breakeven
    s = re.sub(r"\bSTOP\s*[- ]?\s*LOSS(?:ES)?\b", " SL ", s)  # stop loss, stoploss
    s = re.sub(r"\bSTOPS?\b", " SL ", s)
    s = re.sub(r"\bS/L\b|\bSLS\b", " SL ", s)
    s = re.sub(r"\bENTRY\s+(?:PRICE|POINT|LEVEL)\b|\bENTRIES\b|\bOPEN\s+PRICE\b", " ENTRY ", s)
    s = s.replace("@", " AT ").replace("=", " EQUALS ")
    s = re.sub(r"[^\w\n]+", " ", s)                           # emojis, punctuation
    s = re.sub(r"[ \t]+", " ", s)
    return s


def classify_breakeven(text: str, is_reply: bool = False,
                       symbols: Optional[Iterable[str]] = None) -> dict:
    """Decide whether `text` orders the stop-loss moved to break-even.

    Returns {"be": True, "symbol_raw": str|None, "direction": "BUY"|"SELL"|None}
    or      {"be": False, "why": "<code>"}.
    """
    if not text or not text.strip():
        return {"be": False, "why": "empty"}
    if "?" in text:
        return {"be": False, "why": "question"}
    norm = _normalise(text)
    toks = norm.split()
    if len(toks) > _MAX_WORDS:
        return {"be": False, "why": "commentary_too_long"}
    if not any(p.search(norm) for p in _PATTERNS):
        return {"be": False, "why": "no_breakeven_order"}
    words = set(toks)
    if words & _NEGATION:
        return {"be": False, "why": "negated"}
    if words & _CONDITION:
        return {"be": False, "why": "conditional"}
    if words & _OPTIONAL:
        return {"be": False, "why": "optional_advice"}
    if words & _PARTIAL:
        return {"be": False, "why": "partial_not_supported"}

    syms = {s.upper() for s in (symbols or [])} | {"GOLD", "XAUUSD", "XAU"}
    symbol_raw = next((t for t in toks if t in syms), None)
    buy, sell = bool(words & _BUY), bool(words & _SELL)
    direction = "BUY" if buy and not sell else "SELL" if sell and not buy else None
    return {"be": True, "symbol_raw": symbol_raw, "direction": direction}
