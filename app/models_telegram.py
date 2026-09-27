"""
================================================================================
  app/models_telegram.py  --  TELEGRAM COPIER MODELS
================================================================================

  A separate file on purpose. These three tables are additive and self-contained,
  so keeping them out of models.py means the Telegram Copier can be added,
  changed or removed without ever editing the file that 200 live accounts depend
  on for licences, executions and MT5 credentials.

  The tables are created by add_telegram_tables.py, not by create_all().

  ---- THE PRODUCT RULE THESE TABLES ENFORCE ----------------------------------

  Each user connects their own Telegram account and adds their own sources.
  A signal from a user's source reaches only that user's MT5 account. Every
  table here is keyed by license_id, and every query must filter on it.
================================================================================
"""

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.database import Base

# Imported for its side effect: it registers the licenses table on the shared
# Base before the ForeignKeys below are resolved. Without it, importing this
# module on its own raises NoReferencedTableError.
from app.models import License  # noqa: F401


class TelegramAccount(Base):
    """The Telegram account a user has connected. One per licence in v1."""

    __tablename__ = "telegram_accounts"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), unique=True,
                        nullable=False, index=True)

    tg_user_id = Column(String, nullable=True)
    tg_username = Column(String, nullable=True)

    # Masked only: "+2547****123". The full number is never stored.
    phone_masked = Column(String, nullable=True)

    # Fernet-encrypted MTProto session, via app.security_utils.encrypt_secret.
    #
    # Treated exactly like an MT5 password, and then some: never logged, never
    # returned by any endpoint (not even to its owner), never shown in admin,
    # never put in an error message, decrypted only inside the listener.
    #
    # Be clear-eyed about what the encryption buys. It protects against a
    # database dump, a leaked backup, or anyone reading Postgres. It does NOT
    # protect against someone who gets onto the VPS, because the listener must
    # decrypt to log in, so the key lives in that environment by necessity.
    # The database is not the weak point here; the VPS is. That is exactly why
    # users are told to connect a SECOND Telegram account -- it does not lower
    # the chance of a breach, it lowers what a breach is worth.
    session_encrypted = Column(Text, nullable=True)

    # LINKED | DISCONNECTED | LIMITED | REVOKED
    status = Column(String, default="LINKED")

    # Listener heartbeat for this session. When it goes stale the dashboard must
    # say "copier offline" out loud. Silence is how Send-to-MT5 died unnoticed
    # for six and a half weeks in August while the app kept promising trades.
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    linked_at = Column(DateTime(timezone=True), server_default=func.now())
    consent_version = Column(String, nullable=True)
    consent_at = Column(DateTime(timezone=True), nullable=True)


class TelegramSource(Base):
    """One channel or group a user follows, with its own limits.

    Limits are per source, not global, because "I trust this admin with 0.05 and
    that one with 0.01" is the actual thing users want and a single global cap
    cannot express it.
    """

    __tablename__ = "telegram_sources"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), nullable=False,
                        index=True)

    chat_id = Column(String, nullable=False, index=True)
    chat_title = Column(String, nullable=True)
    invite_url = Column(String, nullable=True)
    is_private = Column(Boolean, default=False)

    enabled = Column(Boolean, default=True)

    # SHADOW parses, validates and logs, and never trades. Every new source
    # starts here; only the user can move it to LIVE.
    mode = Column(String, default="SHADOW")

    copy_buy = Column(Boolean, default=True)
    copy_sell = Column(Boolean, default=True)
    copy_sl = Column(Boolean, default=True)
    copy_tp = Column(Boolean, default=True)
    copy_closures = Column(Boolean, default=True)

    # Empty means "whatever this account already has enabled". It is never a way
    # to trade a symbol the user has not set a lot for -- the executor refuses
    # those regardless, and that rule stays.
    allowed_symbols = Column(String, nullable=True)

    # Per-trade ceiling, applied after the user's own lot is resolved.
    max_lot = Column(Float, nullable=True)

    # PER-VOLUME limits. These are the ones that actually protect an account:
    # max_lot does nothing about a channel posting fifteen signals in an hour,
    # and these accounts have already been seen hitting "No money" 203 times in
    # a single day.
    max_trades_per_day = Column(Integer, default=5)
    max_open_positions = Column(Integer, default=2)

    # Broker time, "HH:MM". Null means any time.
    trade_from = Column(String, nullable=True)
    trade_to = Column(String, nullable=True)

    require_sl = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("license_id", "chat_id",
                         name="uq_telegram_source_license_chat"),
    )


class TelegramSignalLog(Base):
    """Every message seen, and what became of it.

    Two jobs at once.

    It is the feed the user reads. That answers the commonest complaint in
    copier products -- "it missed my trade" -- without anyone reading a log
    file, and it is what makes shadow mode a sellable feature rather than a
    testing phase.

    It is also the dedupe table, through the unique constraint below. The key
    includes license_id deliberately: two users following the same channel must
    both get the trade, and keyed on (chat_id, message_id) alone the first user
    through the door would consume the slot while everyone else was silently
    skipped -- the worst class of bug, because it looks like nothing happened.
    """

    __tablename__ = "telegram_signal_log"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), nullable=False,
                        index=True)
    source_id = Column(Integer, ForeignKey("telegram_sources.id"), nullable=True)
    chat_id = Column(String, nullable=False)
    message_id = Column(String, nullable=False)

    raw_text = Column(Text, nullable=True)

    # All four come from the DATABASE clock. Received and executed happen on the
    # VPS, parsed and validated on Render; stamped from local clocks these
    # intervals would be meaningless and occasionally negative.
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    parsed_at = Column(DateTime(timezone=True), nullable=True)
    validated_at = Column(DateTime(timezone=True), nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)

    # RECEIVED | REFUSED_PARSE | REFUSED_RISK | SHADOW | QUEUED | PLACED
    # | EXPIRED
    outcome = Column(String, default="RECEIVED")
    reason = Column(String, nullable=True)
    parsed_json = Column(Text, nullable=True)

    # trade_executions.id once queued, and the broker ticket once filled.
    execution_id = Column(Integer, nullable=True)
    mt5_ticket = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("license_id", "chat_id", "message_id",
                         name="uq_telegram_signal_once"),
    )
