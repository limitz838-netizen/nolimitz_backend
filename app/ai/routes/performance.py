from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.database import SessionLocal

from app.ai.models.ai_trade_history import (
    AITradeHistory
)


router = APIRouter(
    prefix="/api/ai",
    tags=["AI Performance"]
)


@router.get("/performance")
def get_performance():

    db: Session = SessionLocal()

    trades = (
        db.query(AITradeHistory)
        .all()
    )

    total_trades = len(trades)

    wins = len([
        t for t in trades
        if t.result == "WIN"
    ])

    losses = len([
        t for t in trades
        if t.result == "LOSS"
    ])

    open_trades = len([
        t for t in trades
        if t.result == "OPEN"
    ])

    total_profit = sum([
        t.profit for t in trades
    ])

    win_rate = 0

    if total_trades > 0:

        win_rate = round(
            (wins / total_trades) * 100,
            2
        )

    latest_trades = []

    recent = (
        db.query(AITradeHistory)
        .order_by(
            AITradeHistory.id.desc()
        )
        .limit(10)
        .all()
    )

    for trade in recent:

        latest_trades.append({

            "symbol":
                trade.symbol,

            "signal":
                trade.signal,

            "result":
                trade.result,

            "profit":
                trade.profit,

            "confidence":
                trade.confidence
        })

    db.close()

    return {

        "total_trades":
            total_trades,

        "wins":
            wins,

        "losses":
            losses,

        "open_trades":
            open_trades,

        "win_rate":
            win_rate,

        "total_profit":
            round(total_profit, 2),

        "latest_trades":
            latest_trades
    }