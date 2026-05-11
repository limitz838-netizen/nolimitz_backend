from dotenv import load_dotenv
load_dotenv(".env.local", override=True)

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL")

print("DB USED =", DATABASE_URL)

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# Fix postgres:// issue
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine_kwargs = {
    "pool_pre_ping": True,     # ✅ checks dead connections
    "pool_recycle": 300,       # 🔥 FIXES YOUR ERROR (VERY IMPORTANT)
    "pool_size": 5,            # ✅ prevents too many connections
    "max_overflow": 10,        # ✅ allows burst safely
}

if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()