import time
import logging
from datetime import datetime

import MetaTrader5 as mt5

from app.database import SessionLocal
from app.models import ClientMT5Account

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

# =========================================================
# MT5 CONFIG
# =========================================================

TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

MAX_VERIFY_PER_LOOP = 5

VERIFY_LOOP_DELAY = 10

LOGIN_CACHE_SECONDS = 30

LAST_LOGIN_TIMES = {}

LAST_MT5_REFRESH = time.time()

# =========================================================
# ALLOWED BROKERS
# =========================================================

ALLOWED_SERVERS = [

    # EXNESS
    "Exness-MT5Real",
    "Exness-MT5Real2",
    "Exness-MT5Real3",
    "Exness-MT5Real4",
    "Exness-MT5Real5",
    "ExnessKE-MT5Real",
    "ExnessKE-MT5Real2",
    "ExnessKE-MT5Real3",
    "ExnessKE-MT5Real21",

    # DERIV
    "Deriv-Server",
    "Deriv-Demo",

    # FBS
    "FBS-Demo",
    "FBS-Real",

    # IC MARKETS
    "ICMarketsSC-MT5",
    "ICMarketsSC-Demo",
]

# =========================================================
# INITIALIZE MT5
# =========================================================

mt5.shutdown()

initialized = mt5.initialize(
    path=TERMINAL_PATH
)

if not initialized:

    logger.critical(
        "MT5 INIT FAILED"
    )

    raise SystemExit(1)

logger.info(
    "🚀 MT5 Verification Worker Started"
)

# =========================================================
# MAIN LOOP
# =========================================================

while True:

    db = None

    try:

        # =========================================================
        # MT5 HEARTBEAT
        # =========================================================

        terminal_info = mt5.terminal_info()

        if not terminal_info:

            logger.warning(
                "MT5 terminal disconnected"
            )

            mt5.shutdown()

            time.sleep(2)

            mt5.initialize(
                path=TERMINAL_PATH
            )

        # =========================================================
        # SAFE MT5 REFRESH
        # =========================================================

        current_refresh = time.time()

        if current_refresh - LAST_MT5_REFRESH > 1800:

            logger.info(
                "Refreshing MT5 verification terminal"
            )

            mt5.shutdown()

            time.sleep(2)

            mt5.initialize(
                path=TERMINAL_PATH
            )

            LAST_MT5_REFRESH = current_refresh

        # =========================================================
        # DATABASE
        # =========================================================

        db = SessionLocal()

        accounts = (
            db.query(ClientMT5Account)
            .filter(
                ClientMT5Account.is_verified == False
            )
            .limit(MAX_VERIFY_PER_LOOP)
            .all()
        )

        logger.info(
            f"PENDING ACCOUNTS: {len(accounts)}"
        )

        # =========================================================
        # VERIFY USERS
        # =========================================================

        for account in accounts:

            try:

                logger.info(
                    f"VERIFYING {account.login}"
                )

                # =========================================================
                # BROKER VALIDATION
                # =========================================================

                if account.server not in ALLOWED_SERVERS:

                    logger.warning(
                        f"Blocked broker server "
                        f"{account.server}"
                    )

                    account.verification_status = "BLOCKED"

                    db.commit()

                    continue

                # =========================================================
                # LOGIN CACHE
                # =========================================================

                current_time = time.time()

                last_login = LAST_LOGIN_TIMES.get(
                    account.login,
                    0
                )

                if current_time - last_login > LOGIN_CACHE_SECONDS:

                    authorized = mt5.login(
                        login=int(account.login),
                        password=account.password,
                        server=account.server
                    )

                    if authorized:

                        LAST_LOGIN_TIMES[
                            account.login
                        ] = current_time

                else:

                    authorized = True

                # =========================================================
                # LOGIN FAILED
                # =========================================================

                if not authorized:

                    logger.warning(
                        f"LOGIN FAILED "
                        f"{account.login}"
                    )

                    logger.warning(
                        str(mt5.last_error())
                    )

                    account.verification_status = "FAILED"

                    db.commit()

                    continue

                # =========================================================
                # ACCOUNT INFO
                # =========================================================

                info = mt5.account_info()

                if not info:

                    logger.warning(
                        f"ACCOUNT INFO FAILED "
                        f"{account.login}"
                    )

                    account.verification_status = "FAILED"

                    db.commit()

                    continue

                # =========================================================
                # VERIFIED SUCCESSFULLY
                # =========================================================

                account.is_verified = True

                account.verification_status = "VERIFIED"

                account.account_name = info.name

                account.broker_name = info.company

                account.balance = float(
                    info.balance
                )

                account.equity = float(
                    info.equity
                )

                account.last_verified_at = (
                    datetime.utcnow()
                )

                db.commit()

                logger.info(
                    f"✅ VERIFIED "
                    f"{account.login}"
                )

                logger.info(
                    f"NAME: {info.name}"
                )

                logger.info(
                    f"BROKER: {info.company}"
                )

            except Exception as account_error:

                logger.error(
                    f"ACCOUNT ERROR "
                    f"{account.login} "
                    f"{account_error}"
                )

                try:
                    db.rollback()
                except:
                    pass

        # =========================================================
        # CLOSE DB
        # =========================================================

        db.close()

    except Exception as worker_error:

        logger.error(
            f"WORKER ERROR "
            f"{worker_error}"
        )

    finally:

        try:

            if db:
                db.close()

        except:
            pass

    time.sleep(VERIFY_LOOP_DELAY)