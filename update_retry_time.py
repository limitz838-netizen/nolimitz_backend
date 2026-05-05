from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    conn.execute(text("ALTER TABLE trade_executions ADD COLUMN retry_at TIMESTAMP"))
    conn.commit()

print("✅ retry_at column added")