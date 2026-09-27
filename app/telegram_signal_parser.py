"""
================================================================================
  app/telegram_signal_parser.py  --  STRICT TELEGRAM SIGNAL PARSER
================================================================================

  Turns a Telegram message into a trade instruction, or refuses it with a
  reason. Nothing else. No database, no MT5, no imports from the rest of the
  app -- so it can be unit-tested on its own, and it is, in
  test_signal_parser.py.

  ---- THE DESIGN RULE ---------------------------------------------------------

  This is a WHITELIST parser, not an extractor.

  An extractor looks for things it recognises and ignores the rest. That is how
  you end up placing a market BUY with no stop loss from:

      "buy gold around 2648-2652, sl below 2640"

  because it found BUY and GOLD, and quietly dropped "around 2648-2652" and
  "below 2640". The trade opens, it looks fine, and the user's actual risk is
  unbounded.

  A whitelist parser requires every token of the instruction to be something it
  understands. One unrecognised word and the whole message is refused. The cost
  is that some real signals get refused; the benefit is that a refused signal is
  visible in the log and a wrongly-placed one is not.

  ---- WHAT IS AND ISN'T THE INSTRUCTION --------------------------------------

  Real channels post commentary alongside the signal: "risk 1% max", "follow
  money management", "good luck". Refusing on those would refuse nearly
  everything, so:

    * the HEAD -- the line carrying BUY or SELL -- must be fully whitelisted
    * labelled SL / TP / ENTRY lines are parsed
    * every other line is IGNORED, and recorded in `ignored_lines` so the user
      can see what was skipped

  With two exceptions, because both could change the trade:

    * a line mentioning SL or TP without a clean number REFUSES the message.
      Silently dropping a stop loss is the single worst thing this file could
      do.
    * a second BUY/SELL anywhere REFUSES it. Two signals in one message is not
      something to guess at.

  ---- V1 LIMITS ---------------------------------------------------------------

  Pending orders (BUY LIMIT / BUY STOP / SELL LIMIT / SELL STOP) are parsed
  correctly and then refused by the risk gate, because the execution path is
  market-only. They are NEVER converted into market orders: a BUY LIMIT 3720
  filled at market 3740 is a different trade, twenty points worse, with the
  channel's stop now far too close.

  `BUY @ 3725` is treated as MARKET with 3725 recorded as the channel's
  reference price. Whether that reference is still valid is a question about
  live price, which this file cannot see and must not pretend to.
================================================================================
"""

import re

# ---------------------------------------------------------------------------
# Vocabulary. Everything the head may contain. Anything else refuses.
# ---------------------------------------------------------------------------

_BUY_WORDS = {"BUY", "LONG", "BULLISH"}
_SELL_WORDS = {"SELL", "SHORT", "BEARISH"}

# Words that mean "enter at the current price". Harmless noise in the head.
_MARKET_WORDS = {"NOW", "MARKET", "MKT", "INSTANT", "RUNNING", "OPEN"}

# Pending-order types. Parsed, then refused in v1.
_LIMIT_WORDS = {"LIMIT", "LMT"}
_STOP_WORDS = {"STOP", "STP"}

# Introduces a price. Dropped once seen.
_AT_WORDS = {"AT", "@", "FROM"}

# Closure vocabulary.
# NOT "TAKE": "TAKE PROFIT: 1.0920" would route the whole message down the
# closure path and silently discard its stop loss. Found by the test suite.
_CLOSE_WORDS = {"CLOSE", "CLOSED", "EXIT"}
_ALL_WORDS = {"ALL", "EVERYTHING", "EVERY"}

_NUM = r"\d+(?:[.,]\d+)?"

# A labelled stop loss, with its value. Deliberately strict: the number must
# follow the label with only punctuation between them. "SL below 2640" does NOT
# match, and that is the point -- it is refused rather than read as 2640.
#
# Bare "STOP" is deliberately NOT a stop-loss label. It is also the pending
# order type, so "SELL STOP 1.0820" would have had its entry price eaten as a
# stop loss and then been executed at market.
_SL_LABEL = r"(?:SL|S\.L\.?|STOP\s*-?\s*LOSS|STOPLOSS)"
_TP_LABEL = r"(?:TP|T\.P\.?|TAKE\s*-?\s*PROFIT|TAKEPROFIT|TARGET|TG)"
_ENTRY_LABEL = r"(?:ENTRY\s*PRICE|ENTRY|ENTER|EP)"

# The optional index digit binds TIGHT to the label -- "SL1", "TP2" -- with no
# space allowed. Written as `\s*\d?` it ate the first digit of the price, so
# "SL 3720" parsed as 720 and the trade would have carried a stop 3000 points
# away. Also found by the test suite.
_RE_SL = re.compile(rf"\b{_SL_LABEL}\d?\s*[:=@\-]?\s*({_NUM})", re.I)
# [ \t,/]* not [\s,/]* -- \s includes the newline, so the run of numbers would
# cross into the next line and swallow "1.5%" from a risk note.
_RE_TP = re.compile(rf"\b{_TP_LABEL}\d?\s*[:=@\-]?\s*((?:{_NUM}[ \t,/]*)+)", re.I)
_RE_ENTRY = re.compile(rf"\b{_ENTRY_LABEL}\d?\s*[:=@\-]?\s*({_NUM})", re.I)

# ---------------------------------------------------------------------------
# What may be an instrument name
# ---------------------------------------------------------------------------
# The head whitelist needs to tell an instrument from an English word, or
# "good morning everyone" parses as three symbols and one direction.
#
# A token is a candidate instrument if it contains a digit (NAS100, V75,
# BOOM1000), is a known instrument word, or is 6-8 letters beginning with a
# currency/metal code (EURUSD, XAUUSDm, GBPJPYc). Everything else is refused
# by name, which gives the user an honest reason in the feed.

_CCY_PREFIX = {
    "EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "SEK", "NOK",
    "DKK", "PLN", "HUF", "CZK", "TRY", "ZAR", "MXN", "SGD", "HKD", "CNH",
    "CNY", "THB", "INR", "KES", "NGN",
    "XAU", "XAG", "XPT", "XPD", "XCU",
    "BTC", "ETH", "XRP", "SOL", "LTC", "BCH", "ADA", "DOT", "DOG",
}

_KNOWN_INSTRUMENTS = {
    "GOLD", "GOLDUSD", "GOLDSPOT", "SILVER", "SILVERUSD", "COPPER",
    "PLATINUM", "PALLADIUM",
    "BITCOIN", "ETHEREUM", "RIPPLE", "SOLANA", "LITECOIN",
    "OIL", "USOIL", "UKOIL", "WTI", "BRENT", "CRUDE", "CRUDEOIL",
    "NATGAS", "NGAS", "NATURALGAS",
    "DOW", "NASDAQ", "NASDAQ100", "SPX", "RUSSELL", "DAX", "FTSE",
    "NIKKEI", "CAC", "STOXX", "HSI", "ASX",
    "STEPINDEX", "STEP", "MULTISTEPINDEX", "MULTISTEP",
}


def _looks_like_instrument(tok, extra):
    if any(ch.isdigit() for ch in tok):
        return True
    if tok in _KNOWN_INSTRUMENTS or tok in extra:
        return True
    if 6 <= len(tok) <= 8 and tok.isalpha() and tok[:3] in _CCY_PREFIX:
        return True
    return False

# Used to detect that a label is PRESENT even when no clean value follows it.
_RE_SL_MENTION = re.compile(rf"\b{_SL_LABEL}\b", re.I)
_RE_TP_MENTION = re.compile(rf"\b{_TP_LABEL}\b", re.I)


class Refusal(Exception):
    """Carries a machine-readable reason and a sentence for the user."""

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _clean(text):
    """Strip emoji and decoration, keep line structure and numbers intact."""
    if not text:
        return ""
    out = []
    for ch in text:
        if ch == "\n":
            out.append("\n")
        elif ord(ch) > 127:
            # Emoji, arrows, box characters -> a space. Channels lean on them
            # heavily for layout and none of them carry meaning we need.
            out.append(" ")
        elif ch in "*_`~|[]()\"'":
            out.append(" ")
        else:
            out.append(ch)
    s = "".join(out)
    # Collapse runs of spaces/tabs but never newlines.
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _to_float(raw):
    return float(str(raw).replace(",", "."))


def _numbers(blob):
    return [_to_float(m) for m in re.findall(_NUM, blob)]


def parse_signal(text, extra_symbols=None):
    """Parse a Telegram message.

    `extra_symbols` widens what counts as an instrument name. The router passes
    the keys of the copier's own `_SYNONYM_LOOKUP`, so every instrument the
    platform already knows is accepted here without this file keeping a second
    list that would drift from it.

    Returns a dict on success:

        {"kind": "OPEN",
         "symbol_raw": "XAUUSD",     # NOT canonicalised -- the risk gate does
                                     #   that with the copier's own _canonical
         "direction": "BUY",
         "entry_type": "MARKET" | "LIMIT" | "STOP",
         "entry_price": 3725.0 | None,
         "stop_loss": 3720.0 | None,
         "take_profits": [3740.0, 3750.0],
         "ignored_lines": ["risk 1% max"]}

    or for a closure:

        {"kind": "CLOSE", "symbol_raw": "XAUUSD", "direction": "BUY" | None}

    Raises Refusal on anything it will not stand behind.
    """
    extra = {s.upper() for s in (extra_symbols or ())}

    cleaned = _clean(text)
    if not cleaned:
        raise Refusal("empty_message")

    upper = cleaned.upper()

    # ---- closures first ---------------------------------------------------
    # Checked before the open path so "CLOSE XAUUSD BUY" is not read as a BUY.
    words = set(re.findall(r"[A-Z@]+", upper))
    if words & _CLOSE_WORDS:
        return _parse_close(upper, words)

    # ---- pull the labelled fields out of the whole message ----------------
    stop_loss = None
    take_profits = []
    entry_price_labelled = None
    spans = []

    m = _RE_SL.search(upper)
    if m:
        stop_loss = _to_float(m.group(1))
        spans.append(m.span())
    elif _RE_SL_MENTION.search(upper):
        # A stop loss was mentioned and we could not read it. Refusing is the
        # only safe answer: the alternative is a trade with no stop, from a
        # message whose author clearly intended one.
        raise Refusal("sl_ambiguous",
                      "A stop loss is mentioned but no clear price follows it.")

    for m in _RE_TP.finditer(upper):
        take_profits.extend(_numbers(m.group(1)))
        spans.append(m.span())
    if not take_profits and _RE_TP_MENTION.search(upper):
        raise Refusal("tp_ambiguous",
                      "A take profit is mentioned but no clear price follows it.")

    m = _RE_ENTRY.search(upper)
    if m:
        entry_price_labelled = _to_float(m.group(1))
        spans.append(m.span())

    # Blank out what we consumed so the head scan cannot see it again.
    chars = list(upper)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    remainder = "".join(chars)

    # ---- find the head ----------------------------------------------------
    head_line = None
    ignored = []
    for raw_line in remainder.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        line_words = set(re.findall(r"[A-Z@]+", line))
        has_dir = bool(line_words & (_BUY_WORDS | _SELL_WORDS))
        if has_dir:
            if head_line is not None:
                raise Refusal(
                    "multiple_signals",
                    "More than one BUY/SELL in this message. Send one signal "
                    "per message.")
            head_line = line
        elif re.search(r"[A-Z0-9]", line):
            # Real commentary. Kept for the log so the user can see it was seen
            # and skipped, rather than wondering what happened to it.
            ignored.append(line.strip())

    if head_line is None:
        raise Refusal("not_a_signal", "No BUY or SELL found.")

    # ---- whitelist the head ----------------------------------------------
    direction = None
    entry_type = "MARKET"
    entry_price = None
    symbol_raw = None

    tokens = [t for t in re.split(r"[\s,;:]+", head_line) if t]
    for tok in tokens:
        if tok in _BUY_WORDS or tok in _SELL_WORDS:
            if direction is not None:
                raise Refusal("multiple_signals", "Two directions in one line.")
            direction = "BUY" if tok in _BUY_WORDS else "SELL"
            continue
        if tok in _MARKET_WORDS:
            continue
        if tok in _AT_WORDS:
            continue
        if tok in _LIMIT_WORDS:
            entry_type = "LIMIT"
            continue
        if tok in _STOP_WORDS:
            entry_type = "STOP"
            continue
        if re.fullmatch(_NUM, tok):
            if entry_price is not None:
                # Two bare numbers is a range ("2648 2652") or a typo. Either
                # way we do not know which one the author meant.
                raise Refusal(
                    "entry_ambiguous",
                    "More than one entry price. A price range cannot be "
                    "traded as a single entry.")
            entry_price = _to_float(tok)
            continue
        if re.fullmatch(r"[A-Z0-9]{2,20}", tok) and _looks_like_instrument(tok, extra):
            if symbol_raw is not None:
                raise Refusal("multiple_symbols",
                              f"More than one instrument named "
                              f"({symbol_raw} and {tok}).")
            symbol_raw = tok
            continue
        # Anything else. "AROUND", "2648-2652", "BETWEEN", "ZONE", "MORNING" all
        # land here, and all of them mean the instruction is not precise enough
        # to trade.
        raise Refusal("unrecognised_token",
                      f"'{tok}' is not something this parser understands, so "
                      f"the whole signal was refused rather than guessed at.")

    if direction is None:
        raise Refusal("not_a_signal", "No BUY or SELL found.")
    if symbol_raw is None:
        raise Refusal("no_symbol", "No instrument named.")

    if entry_price is None:
        entry_price = entry_price_labelled

    # A pending type with no price is not a pending order, it is a word.
    # "BUY STOP" alone almost always means "buy, stop loss below" -- refuse
    # rather than pick one reading.
    if entry_type in ("LIMIT", "STOP") and entry_price is None:
        raise Refusal("pending_without_price",
                      f"{entry_type} order with no price given.")

    return {
        "kind": "OPEN",
        "symbol_raw": symbol_raw,
        "direction": direction,
        "entry_type": entry_type,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profits": take_profits,
        "ignored_lines": ignored,
    }


def _parse_close(upper, words):
    """CLOSE XAUUSD / CLOSE XAUUSD BUY / CLOSE HALF ...

    A bare "close all" is refused: it is not scoped to an instrument, and a
    message that broad must never reach an account.
    """
    if words & _ALL_WORDS and not (words - _CLOSE_WORDS - _ALL_WORDS):
        raise Refusal("close_all_refused",
                      "A close-all instruction is too broad to act on.")

    direction = None
    symbol_raw = None
    for tok in [t for t in re.split(r"[\s,;:]+", upper) if t]:
        if tok in _CLOSE_WORDS or tok in _MARKET_WORDS or tok in _AT_WORDS:
            continue
        if tok in _BUY_WORDS or tok in _SELL_WORDS:
            direction = "BUY" if tok in _BUY_WORDS else "SELL"
            continue
        if tok in _ALL_WORDS:
            continue
        if re.fullmatch(_NUM, tok):
            continue                     # "close 50%" -> partial, ignored in v1
        if re.fullmatch(r"[A-Z0-9]{2,20}", tok):
            if symbol_raw is None:
                symbol_raw = tok
            continue
        # Unknown words in a closure are not fatal the way they are in an open:
        # "close XAUUSD now please" is unambiguous. Only the instrument matters.

    if symbol_raw is None:
        raise Refusal("close_without_symbol",
                      "A close instruction with no instrument named.")

    return {"kind": "CLOSE", "symbol_raw": symbol_raw, "direction": direction}
