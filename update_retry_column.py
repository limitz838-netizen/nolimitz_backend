from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    conn.execute(text("ALTER TABLE trade_executions ADD COLUMN retry_count INTEGER DEFAULT 0;"))
    conn.commit()

print("DONE ✅ retry_count added")