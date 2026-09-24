"""
================================================================================
  TELEGRAM SIGNAL COPIER  —  app/routers/telegram_copier.py
================================================================================

  WHAT IT DOES
  You post a signal in your Telegram channel. It reaches every NOLIMITZ PRO
  account within seconds. No dashboard, no master MT5 terminal.

      BUY XAUUSD TP:2726 SL:2620
      SELL NAS100 TP:19850 SL:19980

  To close, REPLY to the original signal message with:

      CLOSE

  ── IT STARTS SWITCHED OFF ───────────────────────────────────────────────────

  TELEGRAM_ENABLED defaults to false. Deploying this file changes nothing and
  fires no trades. You turn it on deliberately, after a test post, once the
  logs show it parsing what you actually write. A feature that starts live is
  a feature that surprises you.

  ── THE MESSAGE ID IS THE TICKET ─────────────────────────────────────────────

      master_ticket = "TG-<chat_id>-<message_id>"

  Two things fall out of that for free. Telegram redelivers an update when it
  does not get a fast 200, and a stable ticket makes a duplicate impossible.
  And a CLOSE that replies to the signal carries reply_to_message_id, which
  rebuilds the exact same ticket — so closing is unambiguous with nothing to
  type and nothing to look up.

  ── IT REFUSES RATHER THAN GUESSES ───────────────────────────────────────────

  Anything that is not exactly one action, one symbol, at most one TP and at
  most one SL is IGNORED and logged with a reason. It is never interpreted.

  That includes things you will want later — three take-profits, a price range,
  "close half". They are rejected on purpose in this first version. A misparse
  is not a cosmetic bug: it is a real trade, at the customer's own lot size, on
  their real account. The rejects log tells you what you actually post, and the
  format can be widened deliberately once there is evidence instead of a guess.

  ── SETUP ────────────────────────────────────────────────────────────────────

  1. Create a bot: message @BotFather -> /newbot -> copy the token.
  2. Add the bot to your channel as an ADMIN (it cannot read posts otherwise).
  3. Post any message in the channel, then find your channel id:
         https://api.telegram.org/bot<TOKEN>/getUpdates
     The id looks like -1001234567890, minus sign included.
  4. Render environment:

         TELEGRAM_BOT_TOKEN       = 1234567:AA...
         TELEGRAM_WEBHOOK_SECRET  = <any long random string you invent>
         TELEGRAM_CHANNEL_ID      = -1001234567890
         TELEGRAM_EA_ID           = 1          (NOLIMITZ PRO)
         TELEGRAM_ENABLED         = false      (leave false for now)
         WORKER_TOKEN             = <already set>

  5. Register the webhook (once), pasting your own values:

         https://api.telegram.org/bot<TOKEN>/setWebhook
           ?url=https://nolimitz-backend-yfne.onrender.com/telegram/webhook
           &secret_token=<TELEGRAM_WEBHOOK_SECRET>
           &allowed_updates=["channel_post"]

  6. In app/main.py:
         from app.routers import telegram_copier
         app.include_router(telegram_copier.router)

  7. Post a test signal. Check GET /telegram/status and the Render logs. When
     the parse looks right, set TELEGRAM_ENABLED=true.

  ── SECURITY ─────────────────────────────────────────────────────────────────

  The webhook URL is public. Without the secret header check, anyone who found
  it could fire trades into every connected account. Telegram sends
  X-Telegram-Bot-Api-Secret-Token on every call and this refuses anything else.
  Posts from any chat other than TELEGRAM_CHANNEL_ID are ignored outright.
================================================================================
"""

import logging
import os
import re
from typing import Optional, Tuple

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/telegram", tags=["Telegram"])
logger = logging.getLogger("telegram")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
# Comma-separated list. Several source channels are the normal case once a
# listener is feeding this from other people's channels rather than your own.
# Empty means accept any chat, which is only safe while TELEGRAM_ENABLED is
# false — a public webhook with no chat allowlist will take trades from anyone
# who guesses the URL and the secret.
CHANNEL_IDS = {
    c.strip() for c in os.getenv("TELEGRAM_CHANNEL_ID", "").split(",") if c.strip()
}
EA_ID = int(os.getenv("TELEGRAM_EA_ID", "1"))

# THE KILL SWITCH. Off unless the value is exactly "true". Anything else —
# unset, "false", "1", a typo — means off, because the safe reading of an
# ambiguous setting is "do not place trades".
ENABLED = os.getenv("TELEGRAM_ENABLED", "false").strip().lower() == "true"

# Optional. Empty means accept whatever the copier can match. Set it to a
# comma-separated list to refuse anything else, which catches typos like
# XAUUS before they become 85 failed executions.
ALLOWED_SYMBOLS = {
    s.strip().upper() for s in os.getenv("TELEGRAM_ALLOWED_SYMBOLS", "").split(",")
    if s.strip()
}

BACKEND_BASE = os.getenv(
    "BACKEND_BASE_URL", "https://nolimitz-backend-yfne.onrender.com").rstrip("/")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")

TG_API = "https://api.telegram.org/bot{token}/{method}"


# =============================================================================
#  PARSING
# =============================================================================

# Emoji, arrows and box-drawing that channels decorate signals with. Stripped
# before parsing so "🔥 BUY XAUUSD 🔥" is read as "BUY XAUUSD" — decoration
# never changes meaning, so removing it is safe in a way that guessing is not.
_DECORATION = re.compile(
    r"[\U0001F000-\U0001FAFF←-⇿⌀-➿⬀-⯿"
    r"️‍─-╿*_`~]"
)

_ACTION = re.compile(r"(?<![A-Z0-9])(BUY|SELL)(?![A-Z0-9])")
_CLOSE = re.compile(r"^\s*CLOSE\s*$")
_SYMBOL_OK = re.compile(r"^[A-Z0-9][A-Z0-9._/]{1,19}$")
_NUMBER = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_LABELLED = re.compile(r"^(TP|SL)[:=]?([0-9]+(?:\.[0-9]+)?)?$")


def normalise(raw: str) -> str:
    """Strip decoration and collapse whitespace. Meaning is never changed."""
    s = _DECORATION.sub(" ", raw or "")
    s = s.replace("\n", " ").replace("\t", " ")
    s = s.replace(",", " ").replace(";", " ")
    return re.sub(r"\s+", " ", s).strip().upper()


def parse_signal(raw: str) -> Tuple[Optional[dict], str]:
    """Turn a channel post into a trade, or explain why it will not.

    WHITELIST, NOT EXTRACTION. Every token must be recognised as one of:
    the action, the symbol, a labelled TP/SL, or a single bare entry price.
    One unrecognised word and the whole message is refused.

    That inversion is the entire safety property, and it was not obvious.
    An earlier version searched for the parts it wanted and ignored the rest,
    which read "buy gold around 2648-2652, sl below 2640" as a live market BUY
    on GOLD with NO STOP LOSS — the words "around" and "below" were simply not
    looked at. It also read "TP 2660 2670 2680" as TP 2660 and silently threw
    away the other two.

    Refusing on anything unrecognised makes both impossible: vague prose can
    never survive, because prose is made of words this does not know.

    Returns (signal or None, reason); reason always explains the outcome.
    """
    text_up = normalise(raw)
    if not text_up:
        return None, "empty_message"

    if _CLOSE.match(text_up):
        return {"kind": "close"}, "close"

    tokens = text_up.split(" ")

    actions = [i for i, t in enumerate(tokens) if t in ("BUY", "SELL")]
    if not actions:
        return None, "no_buy_or_sell"
    if len(actions) > 1:
        return None, f"multiple_actions({len(actions)})"

    idx = actions[0]
    if idx != 0:
        # Anything before the action is a header, a comment, or prose. Since
        # it is not understood, it is not ignored either.
        return None, f"text_before_action({tokens[0][:16]!r})"

    if idx + 1 >= len(tokens):
        return None, "no_symbol_after_action"

    action = tokens[idx].lower()
    symbol = tokens[idx + 1].strip(".:")
    if not _SYMBOL_OK.match(symbol):
        return None, f"bad_symbol({symbol[:20]!r})"
    if ALLOWED_SYMBOLS and symbol not in ALLOWED_SYMBOLS:
        return None, f"symbol_not_allowed({symbol})"

    tp = sl = None
    entry_seen = 0
    expect = None          # set to "TP"/"SL" when a bare label awaits its value

    for tok in tokens[idx + 2:]:
        if expect:
            if not _NUMBER.match(tok):
                return None, f"{expect}_without_number({tok[:16]!r})"
            if expect == "TP":
                if tp is not None:
                    return None, "multiple_tp_values(2)"
                tp = tok
            else:
                if sl is not None:
                    return None, "multiple_sl_values(2)"
                sl = tok
            expect = None
            continue

        m = _LABELLED.match(tok)
        if m:
            label, value = m.group(1), m.group(2)
            if value is None:
                expect = label          # value is the next token
                continue
            if label == "TP":
                if tp is not None:
                    return None, "multiple_tp_values(2)"
                tp = value
            else:
                if sl is not None:
                    return None, "multiple_sl_values(2)"
                sl = value
            continue

        if _NUMBER.match(tok):
            # One unlabelled number is the entry price and is ignored — orders
            # go to market. A SECOND one is ambiguous: an extra take-profit, a
            # range, a lot size. Refuse rather than decide.
            entry_seen += 1
            if entry_seen > 1:
                return None, f"unlabelled_numbers({entry_seen})"
            continue

        return None, f"unrecognised_token({tok[:20]!r})"

    if expect:
        return None, f"{expect}_without_number(end_of_message)"

    return {
        "kind": "open",
        "action": action,
        "symbol": symbol,
        "tp": tp,
        "sl": sl,
    }, "ok"


# =============================================================================
#  PLUMBING
# =============================================================================

def ensure_table(db: Session):
    """Dedupe store. UNIQUE on (chat_id, message_id) is the real protection.

    Telegram redelivers when it does not get a prompt 200, so "we already
    handled this" has to be enforced by the database, not by a check that two
    overlapping deliveries would both pass.
    """
    db.execute(text("""
        create table if not exists telegram_signals (
            id           serial primary key,
            chat_id      varchar(40)  not null,
            message_id   bigint       not null,
            master_ticket varchar(80) not null,
            kind         varchar(10)  not null,
            symbol       varchar(40),
            action       varchar(10),
            created_at   timestamptz  not null default now(),
            constraint telegram_signals_unique unique (chat_id, message_id)
        )
    """))
    db.commit()


def claim(db: Session, chat_id: str, message_id: int, ticket: str,
          kind: str, symbol: Optional[str], action: Optional[str]) -> bool:
    """Reserve this message. False means another delivery already has it."""
    row = db.execute(text("""
        insert into telegram_signals
            (chat_id, message_id, master_ticket, kind, symbol, action)
        values (:c, :m, :t, :k, :s, :a)
        on conflict on constraint telegram_signals_unique do nothing
        returning id
    """), {"c": chat_id, "m": message_id, "t": ticket, "k": kind,
           "s": symbol, "a": action}).first()
    db.commit()
    return row is not None


def release(db: Session, chat_id: str, message_id: int):
    """Undo a claim when the trade did not go out, so a retry can work."""
    db.execute(text("delete from telegram_signals "
                    "where chat_id = :c and message_id = :m"),
               {"c": chat_id, "m": message_id})
    db.commit()


def tg_reply(chat_id, message_id: Optional[int], text_body: str):
    """Post confirmation back into the channel.

    Best effort — a failed reply must never fail a trade that already went
    out. But it matters: a copier that works silently is one you stop
    trusting, and then stop using.
    """
    if not BOT_TOKEN:
        return
    try:
        payload = {"chat_id": chat_id, "text": text_body,
                   "disable_notification": True}
        if message_id:
            payload["reply_to_message_id"] = message_id
        requests.post(TG_API.format(token=BOT_TOKEN, method="sendMessage"),
                      json=payload, timeout=10)
    except Exception as e:
        logger.warning("telegram reply failed: %s", e)


def call_copier(path: str, body: dict) -> Tuple[bool, str, int]:
    """Post to this backend's own copier endpoint.

    Deliberately over HTTP rather than importing the handler. The copier owns
    symbol matching, lot sizing and fan-out; going through its public contract
    means this file cannot drift out of step with it. One extra round trip is
    nothing against the seconds the trade then takes to reach accounts.
    """
    headers = {"Content-Type": "application/json"}
    if WORKER_TOKEN:
        headers["X-Worker-Token"] = WORKER_TOKEN
    bearer = os.getenv("COPIER_BEARER_TOKEN", "")
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    try:
        r = requests.post(f"{BACKEND_BASE}{path}", json=body,
                          headers=headers, timeout=30)
    except Exception as e:
        return False, f"request failed: {e}", 0

    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:200]}", 0
    try:
        data = r.json()
        return True, "ok", int(data.get("total_created") or 0)
    except Exception:
        return True, "ok (unparsed response)", 0


# =============================================================================
#  WEBHOOK
# =============================================================================

@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Telegram posts every channel message here.

    ALWAYS returns 200. A non-2xx makes Telegram retry the same update for
    hours, and there is nothing a retry could fix about a message that does not
    parse. Rejections are recorded in the response body and the log, not in the
    status code.
    """
    # Secret first, before reading anything. This URL is public.
    if not WEBHOOK_SECRET:
        logger.error("TELEGRAM_WEBHOOK_SECRET not set — refusing all webhooks")
        return {"ok": True, "ignored": "not_configured"}
    if x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        logger.warning("telegram webhook: bad or missing secret token")
        raise HTTPException(status_code=401, detail="Invalid secret token")

    try:
        update = await request.json()
    except Exception:
        return {"ok": True, "ignored": "not_json"}

    post = update.get("channel_post") or update.get("message") or {}
    chat = post.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    message_id = post.get("message_id")
    body_text = post.get("text") or post.get("caption") or ""

    if not chat_id or not message_id:
        return {"ok": True, "ignored": "no_message"}

    # Only channels you listed. Anyone can add a bot to their own group, and
    # the listener could be pointed anywhere, so without this a stranger's chat
    # could place trades on your customers' accounts.
    if CHANNEL_IDS and chat_id not in CHANNEL_IDS:
        logger.info("ignoring post from chat %s (not in TELEGRAM_CHANNEL_ID)",
                    chat_id)
        return {"ok": True, "ignored": "wrong_chat"}

    signal, reason = parse_signal(body_text)

    if signal is None:
        logger.info("telegram msg %s NOT a signal (%s): %r",
                    message_id, reason, body_text[:120])
        return {"ok": True, "ignored": reason}

    ensure_table(db)

    # ---- CLOSE ------------------------------------------------------------
    if signal["kind"] == "close":
        reply_to = (post.get("reply_to_message") or {}).get("message_id")
        if not reply_to:
            tg_reply(chat_id, message_id,
                     "⚠️ CLOSE ignored — reply to the original signal message.")
            return {"ok": True, "ignored": "close_without_reply"}

        orig = db.execute(text(
            "select master_ticket, symbol from telegram_signals "
            "where chat_id = :c and message_id = :m and kind = 'open'"
        ), {"c": chat_id, "m": reply_to}).mappings().first()

        if not orig:
            tg_reply(chat_id, message_id,
                     "⚠️ CLOSE ignored — that message is not a signal I sent.")
            return {"ok": True, "ignored": "close_target_unknown"}

        if not ENABLED:
            tg_reply(chat_id, message_id, "🔕 Copier is OFF — close not sent.")
            return {"ok": True, "parsed": "close", "sent": False,
                    "reason": "TELEGRAM_ENABLED is false"}

        ok, detail, n = call_copier("/copier/close", {
            "ea_id": EA_ID,
            "master_ticket": orig["master_ticket"],
            "symbol": orig["symbol"],
            "comment": "Telegram close",
        })
        if ok:
            tg_reply(chat_id, message_id,
                     f"✅ CLOSE {orig['symbol']} → {n} account(s)")
            logger.info("telegram close %s -> %s accounts",
                        orig["master_ticket"], n)
        else:
            tg_reply(chat_id, message_id, f"❌ CLOSE failed — {detail[:120]}")
            logger.error("telegram close FAILED %s: %s",
                         orig["master_ticket"], detail)
        return {"ok": True, "parsed": "close", "sent": ok, "accounts": n}

    # ---- OPEN -------------------------------------------------------------
    ticket = f"TG-{chat_id}-{message_id}"

    if not ENABLED:
        # Parse and report, place nothing. This is the mode to run in while
        # you check it reads your real posts correctly.
        logger.info("telegram msg %s parsed OK but copier is OFF: %s %s "
                    "tp=%s sl=%s", message_id, signal["action"],
                    signal["symbol"], signal["tp"], signal["sl"])
        tg_reply(chat_id, message_id,
                 f"🔕 Parsed {signal['action'].upper()} {signal['symbol']} "
                 f"(TP:{signal['tp'] or '—'} SL:{signal['sl'] or '—'}) — "
                 f"copier is OFF, no trade sent.")
        return {"ok": True, "parsed": signal, "sent": False,
                "reason": "TELEGRAM_ENABLED is false"}

    if not claim(db, chat_id, message_id, ticket, "open",
                 signal["symbol"], signal["action"]):
        logger.info("telegram msg %s already handled — not duplicating",
                    message_id)
        return {"ok": True, "duplicate": True}

    ok, detail, n = call_copier("/copier/open", {
        "ea_id": EA_ID,
        "master_ticket": ticket,
        "symbol": signal["symbol"],
        "action": signal["action"],
        "sl": signal["sl"],
        "tp": signal["tp"],
        "price": "0",
        "comment": "Telegram signal",
    })

    if not ok:
        # Release so a genuine retry can work. Without this a transient blip
        # would permanently mark the signal as handled and it would never go.
        release(db, chat_id, message_id)
        tg_reply(chat_id, message_id, f"❌ Not sent — {detail[:150]}")
        logger.error("telegram signal %s FAILED: %s", ticket, detail)
        return {"ok": True, "parsed": signal, "sent": False, "error": detail}

    tg_reply(chat_id, message_id,
             f"✅ {signal['action'].upper()} {signal['symbol']} → {n} account(s)"
             + (f"\nTP {signal['tp']}" if signal["tp"] else "")
             + (f"  SL {signal['sl']}" if signal["sl"] else "")
             + "\nReply CLOSE to this message to close it.")
    logger.info("telegram signal %s: %s %s tp=%s sl=%s -> %s accounts",
                ticket, signal["action"], signal["symbol"],
                signal["tp"], signal["sl"], n)
    return {"ok": True, "parsed": signal, "sent": True, "accounts": n,
            "master_ticket": ticket}


@router.get("/status")
def telegram_status(db: Session = Depends(get_db)):
    """Is it configured, is it armed, and what has it done.

    Safe to open in a browser — it reveals no tokens, only whether each is
    present.
    """
    ensure_table(db)
    row = db.execute(text("""
        select count(*) as total,
               count(*) filter (where kind = 'open') as opens,
               max(created_at) as last_signal
        from telegram_signals
    """)).mappings().first()
    return {
        "enabled": ENABLED,
        "bot_token_configured": bool(BOT_TOKEN),
        "webhook_secret_configured": bool(WEBHOOK_SECRET),
        "channels_allowed": sorted(CHANNEL_IDS) or "ANY (unsafe — set TELEGRAM_CHANNEL_ID)",
        "worker_token_configured": bool(WORKER_TOKEN),
        "ea_id": EA_ID,
        "allowed_symbols": sorted(ALLOWED_SYMBOLS) or "any",
        "signals_seen": row["total"],
        "opens_sent": row["opens"],
        "last_signal_at": str(row["last_signal"]) if row["last_signal"] else None,
    }


@router.post("/parse-test")
def parse_test(payload: dict):
    """Check how a message would be read. Places nothing, needs no Telegram.

        curl -X POST .../telegram/parse-test \
          -H "Content-Type: application/json" \
          -d '{"text":"BUY XAUUSD TP:2726 SL:2620"}'

    Use it to try your real wording before pointing anything at live accounts.
    """
    signal, reason = parse_signal(payload.get("text", ""))
    return {"input": payload.get("text", ""), "parsed": signal,
            "reason": reason, "would_place_trade": bool(signal) and ENABLED}
