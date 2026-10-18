"""
================================================================================
  telegram_close_orders.py  --  is this Telegram message an order to CLOSE?
================================================================================

  Pure Python. No database, no MT5, no eval -- text in, decision out.

  WHY THIS IS NARROW
  Channels say "close" all day without meaning it. From one day of real traffic:

      "Hold or close?"                                  a question
      "Close your windows, cause you dont want ..."     a joke
      "GOLD Sell TP1=50PIPS  CLOSED TP1"                a results post
      "You are allowed to close with zero Losses"       advice
      "I close my lowest entry"                         the admin's own trade
      "BOOK TP1 FROM 4144 CLOSE 4139 AT ENTRY IF SATISFIED"   optional, partial

  Acting on any of those would shut customers' trades early. So a message is a
  close order only when it reads as one:

      "Close All positions and wait for my Directions"
      "Let's close all sell positions now and buy XAUUSD now"
      "CLOSE GOLD NOW"          "Exit now"         "Take profit now"
      and, as a REPLY to the signal itself: "close", "exit", "tp now", ...

  RULES
  1. Questions, conditions and advice are never orders:
     "?", IF, SATISFIED, CAN, MAY, ALLOWED, OPTIONAL, WHO, ...
  2. Results posts are never orders: CLOSED, BOOKED, HIT, SECURED, PIPS, ...
  3. Partial closes are refused, not guessed: HALF, 50%, SOME, ENTRY, ZONE,
     LOWER/HIGHER ... -- which positions to keep is the trader's call.
  4. The close verb (CLOSE / EXIT / TAKE PROFIT / BOOK PROFIT / SECURE PROFIT
     / TP NOW) must START its sentence, apart from harmless openers such as
     "Let's", "Guys", "Please", a symbol or a direction.
  5. What follows the verb, up to "and"/"then", may only name WHAT to close
     (all, positions, trades, gold, sells, now ...). "Close your windows"
     fails here.
  6. A bare verb ("Close", "Take profit") counts only as a REPLY to a signal,
     or with NOW / ALL / a symbol / a direction / positions named.
================================================================================
"""

import re
from typing import Iterable, Optional

# ---- vocabulary ---------------------------------------------------------------
_QUESTION_OR_CONDITION = {
    "IF", "SATISFIED", "CAN", "COULD", "MAY", "MIGHT", "ALLOWED", "OPTIONAL",
    "CHOICE", "WHO", "WHOS", "WHO'S", "SHOULD", "WOULD", "OR", "WHETHER",
    "UNLESS", "WHEN", "ONCE", "AFTER", "BEFORE", "UNTIL", "MESSAGE", "DM",
    "COMMENT", "REACT", "VOTE", "ANYONE", "WISH", "WANT", "PREFER",
}
_RESULTS_POST = {
    "CLOSED", "BOOKED", "HIT", "SECURED", "DONE", "PIPS", "PIP", "PIPS'S",
    "CONGRATS", "CONGRATULATIONS", "ALHAMDULILLAH", "ALHAMDULILAH",
    "MASHAALLAH", "SMASHED", "BANKED", "RUNNING", "WON", "WIN", "WINS",
    "RESULT", "RESULTS", "ACHIEVED",
}
_PARTIAL = {
    "HALF", "PARTIAL", "PARTIALS", "PARTIALLY", "SOME", "PART", "ENTRY",
    "ENTRIES", "ZONE", "LOWER", "HIGHER", "LOWEST", "HIGHEST", "LOWE",
    "HIGHERL", "ONE", "TWO", "RUNNER", "RUNNERS", "LAYER", "LAYERS", "FIRST",
    "SECOND", "LAST", "REST", "REMAINING", "LOTS", "LOT", "TP1", "TP2", "TP3",
    "TP4", "TP5",
}
# Words allowed BEFORE the verb in its sentence.
_OPENERS = {
    "LET", "LETS", "S", "PLEASE", "PLS", "PLZ", "GUYS", "TRADERS", "TEAM",
    "FAMILY", "EVERYONE", "EVERYBODY", "FAM", "OK", "OKAY", "ALERT", "UPDATE",
    "URGENT", "NOW", "ALL", "MEMBERS", "VIP", "FOLKS", "HELLO", "HI", "HEY",
    "NOTE", "SIGNAL", "ATTENTION", "IMPORTANT",
}
# Words allowed AFTER the verb, inside the order's own clause.
_SCOPE = {
    "ALL", "NOW", "IT", "THEM", "THIS", "THAT", "THESE", "THOSE", "TRADE",
    "TRADES", "POSITION", "POSITIONS", "ORDER", "ORDERS", "SIGNAL", "SIGNALS",
    "EVERYTHING", "IMMEDIATELY", "ASAP", "PLEASE", "PLS", "HERE", "FULL",
    "FULLY", "OUT", "YOUR", "OUR", "THE", "MARKET", "AT", "GUYS", "TRADERS",
    "TEAM", "FAMILY", "EVERYONE", "QUICKLY", "FAST", "RIGHT", "AWAY", "TODAY",
    "PROFIT", "PROFITS", "OPEN", "ACTIVE", "CURRENT", "OF", "ON",
}
_BUY = {"BUY", "BUYS", "LONG", "LONGS"}
_SELL = {"SELL", "SELLS", "SHORT", "SHORTS"}
_TRADE_NOUNS = {"TRADE", "TRADES", "POSITION", "POSITIONS", "ORDER", "ORDERS",
                "SIGNAL", "SIGNALS", "PROFIT", "PROFITS", "IT", "THEM",
                "EVERYTHING"}
_CLAUSE_BREAK = {"AND", "THEN", "BUT", "WAIT", "RE", "REENTER", "RE-ENTER"}

# Verb phrases, matched on the token list. Longest first.
_VERBS = [
    ("TAKE", "YOUR", "PROFITS"), ("TAKE", "YOUR", "PROFIT"),
    ("TAKE", "PROFITS"), ("TAKE", "PROFIT"),
    ("BOOK", "PROFITS"), ("BOOK", "PROFIT"), ("BOOK", "ALL", "PROFITS"),
    ("SECURE", "PROFITS"), ("SECURE", "PROFIT"),
    ("SECURE", "YOUR", "PROFITS"), ("SECURE", "YOUR", "PROFIT"),
    ("CLOSE",), ("EXIT",), ("TP", "NOW"),
]

_MAX_WORDS = 30


def _tokens(s: str):
    s = s.upper().replace("’", "'")
    s = re.sub(r"\bLET'S\b", "LETS", s)
    return re.findall(r"[A-Z0-9']+|%", s)


def _sentences(text: str):
    for part in re.split(r"[\n.!;]+", text):
        if part.strip():
            yield part


def _match_verb(toks, i):
    for v in _VERBS:
        if tuple(toks[i:i + len(v)]) == v:
            return len(v)
    return 0


def classify_close(text: str, is_reply: bool = False,
                   symbols: Optional[Iterable[str]] = None) -> dict:
    """Decide whether `text` is an order to close.

    Returns {"close": True, "symbol_raw": str|None, "direction": "BUY"|"SELL"|None}
    or      {"close": False, "why": "<code>"}  -- code is for the feed/log.

    `symbols`: the platform's instrument words (XAUUSD, GOLD, ...) so a symbol
    is recognised as one and not as an unknown word.
    """
    if not text or not text.strip():
        return {"close": False, "why": "empty"}
    syms = {s.upper() for s in (symbols or [])} | {"GOLD", "XAUUSD", "XAU"}

    if "?" in text:
        return {"close": False, "why": "question"}

    all_toks = _tokens(text)
    if len(all_toks) > _MAX_WORDS:
        return {"close": False, "why": "commentary_too_long"}
    words = set(all_toks)

    if words & _QUESTION_OR_CONDITION:
        return {"close": False, "why": "condition_or_advice"}
    if words & _RESULTS_POST:
        return {"close": False, "why": "results_post"}
    if "%" in words or words & _PARTIAL:
        return {"close": False, "why": "partial_close_not_supported"}
    # Any price or count in the message: a close AT a level, a "close 2",
    # "+130 pips" -- none of them is a plain close-everything order.
    if any(re.fullmatch(r"\d+(\.\d+)?", t) for t in all_toks):
        return {"close": False, "why": "number_in_close_message"}

    for sentence in _sentences(text):
        toks = _tokens(sentence)
        for i in range(len(toks)):
            n = _match_verb(toks, i)
            if not n:
                continue
            before = toks[:i]
            if any(t not in _OPENERS and t not in syms
                   and t not in _BUY and t not in _SELL for t in before):
                break                      # verb is not leading its sentence
            clause = []
            for t in toks[i + n:]:
                if t in _CLAUSE_BREAK:
                    break
                clause.append(t)
            # "-10pips" after the order is the admin noting the loss, not scope.
            bad = [t for t in clause if t not in _SCOPE and t not in syms
                   and t not in _BUY and t not in _SELL
                   and not re.fullmatch(r"\d+PIPS?", t)]
            if bad:
                return {"close": False, "why": "not_a_trade_close:" + bad[0].lower()}
            if "YOUR" in clause and not (set(clause) & _TRADE_NOUNS):
                return {"close": False, "why": "not_a_trade_close"}

            scope = before + clause
            symbol_raw = next((t for t in scope if t in syms), None)
            buy, sell = bool(set(scope) & _BUY), bool(set(scope) & _SELL)
            direction = "BUY" if buy and not sell else "SELL" if sell and not buy else None
            explicit = bool(set(scope) & ({"NOW", "ALL", "IMMEDIATELY", "ASAP",
                                            "EVERYTHING"} | _TRADE_NOUNS)
                            or symbol_raw or direction
                            or toks[i:i + n] == ["TP", "NOW"])
            if not explicit and not is_reply:
                return {"close": False, "why": "bare_close_not_a_reply"}
            return {"close": True, "symbol_raw": symbol_raw, "direction": direction}
    return {"close": False, "why": "no_close_verb"}
