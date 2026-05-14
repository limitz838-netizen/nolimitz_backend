from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ai.models.ai_market_state import AIMarketState


router = APIRouter(
    prefix="/api/ai",
    tags=["Live AI Market"]
)


@router.get("/live-market")
def get_live_market():

    db: Session = SessionLocal()

    states = (
        db.query(AIMarketState)
        .all()
    )

    results = []

    for state in states:

        results.append({

            "symbol":
                state.symbol,

            "signal":
                state.signal,

            "trend":
                state.trend,

            "confidence":
                state.confidence,

            "entry":
                state.entry,

            "stop_loss":
                state.stop_loss,

            "take_profit":
                state.take_profit,

            "analysis":
                state.analysis,

            "updated_at":
                state.updated_at
        })

    db.close()

    return {
        "markets": results
    }