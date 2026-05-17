from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime
)

from datetime import datetime

from app.database import Base


class AITradeHistory(Base):

    __tablename__ = "ai_trade_history"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    license_id = Column(Integer, nullable=True)

    mt5_login = Column(String, nullable=True)

    symbol = Column(
        String,
        nullable=False
    )

    signal = Column(
        String,
        nullable=False
    )

    trend = Column(
        String,
        nullable=True
    )

    entry_price = Column(
        Float,
        default=0
    )

    stop_loss = Column(
        Float,
        default=0
    )

    take_profit = Column(
        Float,
        default=0
    )

    confidence = Column(
        Integer,
        default=0
    )

    result = Column(
        String,
        default="OPEN"
    )

    profit = Column(
        Float,
        default=0
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    closed_at = Column(
        DateTime,
        nullable=True
    )