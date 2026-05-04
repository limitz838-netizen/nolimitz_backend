import asyncio

from app.database import SessionLocal
from app.services.metaapi_service import MetaApiService
from app.models import TradeExecution
from app.workers.mt5_execution_worker import process_open_execution


async def dispatch_trade(trade_id: int):
    db = SessionLocal()

    try:
        trade = db.query(TradeExecution).get(trade_id)

        if not trade:
            return

        service = MetaApiService()

        await process_open_execution(db, service, trade)

    except Exception as e:
        print(f"Dispatch error: {e}")

    finally:
        db.close()