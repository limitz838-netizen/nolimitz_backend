import time
import uuid
import logging

import MetaTrader5 as mt5

from sqlalchemy.orm import Session

from app.database import SessionLocal

from app.models import (
    ClientMT5Account,
    LiveTrade,
)

# =================================================
# LOGGING
# =================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("ai_management_worker")

# =================================================
# CONFIG
# =================================================

MAX_USERS_PER_LOOP = 100

WORKER_ID = str(uuid.uuid4())[:8]

# =================================================
# MT5 INIT
# =================================================

if not mt5.initialize():

    logger.error(
        "❌ MT5 INITIALIZATION FAILED"
    )

    quit()

logger.info(
    f"🚀 AI Management Worker Started "
    f"| Worker: {WORKER_ID}"
)

# =================================================
# MAIN LOOP
# =================================================

while True:

    db: Session = SessionLocal()

    try:

        logger.info(
            f"💓 Management heartbeat "
            f"| Worker: {WORKER_ID}"
        )

        # =================================================
        # LIVE TRADE MANAGEMENT
        # =================================================

        accounts = (
            db.query(ClientMT5Account)
            .filter(
                ClientMT5Account.is_active == True,
                ClientMT5Account.ai_auto_trade == True
            )
            .limit(MAX_USERS_PER_LOOP)
            .all()
        )

        for account in accounts:

            try:

                authorized = mt5.login(
                    int(account.login),
                    account.password,
                    account.server
                )

                if not authorized:

                    logger.warning(
                        f"MT5 LOGIN FAILED "
                        f"{account.login}"
                    )

                    continue

                positions = mt5.positions_get() or []

                open_trades = (
                    db.query(LiveTrade)
                    .filter(
                        LiveTrade.mt5_login
                        == str(account.login),

                        LiveTrade.status == "OPEN"
                    )
                    .limit(MAX_USERS_PER_LOOP)
                    .all()
                )

                for trade in open_trades:

                    pos = next(

                        (
                            p for p in positions
                            if str(p.ticket)
                            == trade.mt5_ticket
                        ),

                        None
                    )

                    # =====================================
                    # TRADE CLOSED ON MT5
                    # =====================================

                    if not pos:

                        trade.status = "CLOSED"

                        db.commit()

                        logger.info(
                            f"✅ TRADE CLOSED "
                            f"{trade.mt5_ticket}"
                        )

                        continue

                    # =====================================
                    # UPDATE LIVE PROFIT
                    # =====================================

                    trade.profit = round(
                        pos.profit,
                        2
                    )

                    db.commit()

                    logger.info(
                        f"📈 LIVE PROFIT "
                        f"{trade.mt5_ticket} "
                        f"{trade.profit}"
                    )

            except Exception as account_error:

                logger.error(
                    f"ACCOUNT MANAGEMENT ERROR "
                    f"{account.login} "
                    f"{account_error}"
                )

                continue

    except Exception as e:

        logger.error(
            f"MANAGEMENT WORKER ERROR "
            f"{e}"
        )

    finally:

        db.close()

    time.sleep(2)