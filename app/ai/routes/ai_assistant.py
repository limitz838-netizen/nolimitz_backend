from fastapi import APIRouter
from pydantic import BaseModel

from app.ai.services.ai_market_reader import AIMarketReader
from app.ai.services.ai_scanner import AIScanner

router = APIRouter(
    prefix="/api/ai",
    tags=["AI Assistant"]
)


class ChatRequest(BaseModel):
    message: str
    symbol: str = "XAUUSD"


@router.post("/assistant")
def ai_assistant(data: ChatRequest):

    reader = AIMarketReader()

    scanner = AIScanner()

    candles = reader.get_candles(
        symbol=data.symbol
    )

    analysis = scanner.analyze_market(
        candles
    )

    signal = analysis["signal"]

    confidence = analysis["confidence"]

    trend = analysis["trend"]

    response = ""

    if signal == "BUY":

        response = (
            f"{data.symbol} is currently bullish. "
            f"The AI detects buying momentum with "
            f"{confidence}% confidence. "
            f"Trend direction is {trend}. "
            f"A possible BUY opportunity may exist."
        )

    elif signal == "SELL":

        response = (
            f"{data.symbol} is currently bearish. "
            f"The AI detects selling pressure with "
            f"{confidence}% confidence. "
            f"Trend direction is {trend}. "
            f"A possible SELL opportunity may exist."
        )

    else:

        response = (
            f"{data.symbol} is currently ranging. "
            f"The AI does not see a strong setup yet."
        )

    return {
        "symbol": data.symbol,
        "analysis": analysis,
        "assistant_response": response
    }