import time
from sqlalchemy.orm import Session

from app.database import SessionLocal

from app.ai.models.ai_market_state import (
    AIMarketState
)

from app.ai.services.ai_chat_engine import (
    analyze_market
)

from app.models import AISignal


# =========================
# SYMBOLS TO SCAN
# =========================

SYMBOLS = [
    "XAUUSD",
    "EURUSD",
    "GBPUSD",
    "USDJPY"
]


# =========================
# SAVE MARKET STATE
# =========================

def save_market_state():

    db: Session = SessionLocal()

    try:

        for symbol in SYMBOLS:

            print(f"\nSCANNING {symbol}")

            # =========================
            # AI ANALYSIS
            # =========================

            analysis = analyze_market(
                symbol
            )

            # =========================
            # UPDATE LIVE MARKET STATE
            # =========================

            existing = (
                db.query(AIMarketState)
                .filter(
                    AIMarketState.symbol
                    == symbol
                )
                .first()
            )

            if not existing:

                existing = AIMarketState(
                    symbol=symbol
                )

                db.add(existing)

            existing.signal = str(
                analysis["signal"]
            )

            existing.trend = str(
                analysis["trend"]
            )

            existing.confidence = int(
                analysis["confidence"]
            )

            existing.entry = float(
                analysis["current_price"]
            )

            existing.stop_loss = float(
                analysis["stop_loss"]
            )

            existing.take_profit = float(
                analysis["take_profit"]
            )

            existing.analysis = str(
                analysis[
                    "assistant_response"
                ]
            )

            db.commit()

            print(
                f"{symbol} market state updated"
            )

            # =========================
            # CHECK LAST SIGNAL
            # =========================

            existing_signal = (
                db.query(AISignal)
                .filter(
                    AISignal.symbol
                    == symbol
                )
                .order_by(
                    AISignal.id.desc()
                )
                .first()
            )

            # =========================
            # PREVENT DUPLICATES
            # =========================

            create_new_signal = False

            if not existing_signal:

                create_new_signal = True

            elif (
                existing_signal.signal
                != analysis["signal"]
            ):

                create_new_signal = True

            # =========================
            # CREATE NEW SIGNAL
            # =========================

            if (
                create_new_signal
                and analysis["signal"]
                != "WAIT"
            ):

                new_signal = AISignal(

                    symbol=symbol,

                    timeframe="M5",

                    signal=str(
                        analysis["signal"]
                    ),

                    confidence=int(
                        analysis["confidence"]
                    ),

                    entry_price=float(
                        analysis[
                            "current_price"
                        ]
                    ),

                    stop_loss=float(
                        analysis[
                            "stop_loss"
                        ]
                    ),

                    take_profit=float(
                        analysis[
                            "take_profit"
                        ]
                    ),

                    trend=str(
                        analysis["trend"]
                    )
                )

                db.add(new_signal)

                db.commit()

                print(
                    f"NEW AI SIGNAL: "
                    f"{analysis['signal']} "
                    f"{symbol}"
                )

            else:

                print(
                    f"No new signal "
                    f"for {symbol}"
                )

    except Exception as e:

        print(
            "\nWATCHER ERROR:"
        )

        print(e)

    finally:

        db.close()


# =========================
# CONTINUOUS LOOP
# =========================

while True:

    save_market_state()

    time.sleep(60)