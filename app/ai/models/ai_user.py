from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    Float
)
from datetime import datetime

from app.database import Base


class AIUser(Base):
    __tablename__ = "ai_users"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String, nullable=False)

    email = Column(String, unique=True, index=True, nullable=False)

    password_hash = Column(String, nullable=False)

    credits = Column(Integer, default=25)

    trial_active = Column(Boolean, default=True)

    subscription_plan = Column(String, default="free")

    created_at = Column(DateTime, default=datetime.utcnow)

    