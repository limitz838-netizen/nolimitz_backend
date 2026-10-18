"""
app/desk/models.py -- tables for the Desk (Oct 2026)

All three are NEW tables, so Base.metadata.create_all() in app/main.py creates
them on the next Render boot. Nothing here touches an existing table.

  desk_events  one row per thing an agent did (scout saw a setup, size picked a
               lot, entry filled, guard blocked...). The Desk page animates these.
  desk_trades  one row per position the Desk opened, with the numbers needed to
               manage it (initial stop, ATR at entry) and its final result.
  desk_state   one row per desk worker: the latest account snapshot, open
               positions, recent candles and each agent's current status line.

No MT5 password is ever written to any of these tables.
"""
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, JSON, String
from sqlalchemy.sql import func

from app.database import Base


class DeskEvent(Base):
    __tablename__ = "desk_events"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    worker = Column(String(64), nullable=False, index=True)
    symbol = Column(String(32), nullable=True)
    agent = Column(String(16), nullable=False)          # scout|check|size|entry|exit|guard|chief
    level = Column(String(8), nullable=False, default="info")   # info|ok|warn|error
    message = Column(String(240), nullable=False)
    data = Column(JSON, nullable=True)


class DeskTrade(Base):
    __tablename__ = "desk_trades"

    id = Column(Integer, primary_key=True, index=True)
    worker = Column(String(64), nullable=False, index=True)
    ticket = Column(String(32), nullable=False, index=True)
    symbol = Column(String(32), nullable=False)          # canonical, e.g. XAUUSD
    broker_symbol = Column(String(32), nullable=False)   # e.g. XAUUSDm
    direction = Column(Integer, nullable=False)          # +1 long / -1 short
    lot = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    initial_stop = Column(Float, nullable=False)
    current_stop = Column(Float, nullable=False)
    target = Column(Float, nullable=False)
    atr_at_entry = Column(Float, nullable=False)
    risk_usd = Column(Float, nullable=True)
    opened_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_open = Column(Boolean, nullable=False, default=True, index=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    exit_price = Column(Float, nullable=True)
    profit_usd = Column(Float, nullable=True)            # profit + swap + commission from MT5 deals
    r_multiple = Column(Float, nullable=True)
    exit_reason = Column(String(32), nullable=True)      # stop|target|breakeven|trail|bias_flip|manual|unknown
    is_demo = Column(Boolean, nullable=False, default=True)


class DeskState(Base):
    __tablename__ = "desk_state"

    id = Column(Integer, primary_key=True, index=True)
    worker = Column(String(64), unique=True, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    payload = Column(JSON, nullable=False)
