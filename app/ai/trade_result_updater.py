import time
import MetaTrader5 as mt5

from datetime import datetime

from sqlalchemy.orm import Session

from app.database import SessionLocal

from app.ai.models.ai_trade_history import (
    AITradeHistory
)


def update_trade_results():

    db: Session = SessionLocal()

    try:

        open_trades = (
            db.query(AITradeHistory)
            .filter(
                AITradeHistory.result == "OPEN"
            )
            .all()
        )

        positions = mt5.positions_get()

        open_symbols = []

        if positions:

            for pos in positions:

                open_symbols.append(
                    pos.symbol
                )

        for trade in open_trades:

            if trade.symbol not in open_symbols:

                # =========================
                # TRADE CLOSED
                # =========================

                current_price = mt5.symbol_info_tick(
                    trade.symbol
                ).bid

                profit = 0

                # BUY PROFIT
                if trade.signal == "BUY":

                    profit = (
                        current_price
                        - trade.entry_price
                    ) * 100

                # SELL PROFIT
                elif trade.signal == "SELL":

                    profit = (
                        trade.entry_price
                        - current_price
                    ) * 100

                trade.profit = round(
                    profit,
                    2
                )

                if profit > 0:

                    trade.result = "WIN"

                else:

                    trade.result = "LOSS"

                trade.closed_at = (
                    datetime.utcnow()
                )

                db.commit()

                print(
                    f"UPDATED "
                    f"{trade.symbol} "
                    f"{trade.result}"
                )

    except Exception as e:

        print(
            "TRADE RESULT ERROR:",
            e
        )

    finally:

        db.close()


while True:

    update_trade_results()

    time.sleep(30)