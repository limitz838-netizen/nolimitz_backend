from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime
)

from app.database import Base


class AIMarketState(Base):

    __tablename__ = "ai_market_states"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    symbol = Column(String)

    signal = Column(String)

    trend = Column(String)

    confidence = Column(Integer)

    entry = Column(Float)

    stop_loss = Column(Float)

    take_profit = Column(Float)

    analysis = Column(String)

    updated_at = Column(
        DateTime,
        default=datetime.utcnow
    )