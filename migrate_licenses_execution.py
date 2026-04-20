from dotenv import load_dotenv
load_dotenv(dotenv_path=".env.local", override=True)

from sqlalchemy import create_engine, text
import os

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)

statements = [
    "ALTER TABLE licenses ADD COLUMN IF NOT EXISTS execution_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE licenses ADD COLUMN IF NOT EXISTS execution_started_at TIMESTAMP NULL",
]

with engine.begin() as conn:
    for stmt in statements:
        conn.execute(text(stmt))

print("licenses execution migration completed successfully.")