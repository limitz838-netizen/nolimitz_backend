from dotenv import load_dotenv
load_dotenv(dotenv_path=".env.local", override=True)

from app.database import SessionLocal
from app.models import TradeExecution

db = SessionLocal()

try:
    rows = (
        db.query(TradeExecution)
        .filter(TradeExecution.status == "pending")
        .all()
    )

    count = 0
    for row in rows:
        row.status = "failed"
        row.error_message = "Cleared old pending queue before fresh test"
        count += 1

    db.commit()
    print(f"Cleared {count} pending trade executions.")
finally:
    db.close()