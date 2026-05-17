import time
from datetime import datetime

import MetaTrader5 as mt5

from app.database import SessionLocal
from app.models import ClientMT5Account

# =========================
# MT5 TERMINAL PATH
# =========================

TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

print("MT5 Verification Worker Running")

# =========================
# MAIN LOOP
# =========================

while True:

    try:

        db = SessionLocal()

        accounts = (
            db.query(ClientMT5Account)
            .filter(
                ClientMT5Account.is_verified == False
            )
            .all()
        )

        print(f"PENDING ACCOUNTS: {len(accounts)}")

        for account in accounts:

            try:

                print("\n===================")

                print(f"VERIFYING {account.login}")

                mt5.shutdown()

                initialized = mt5.initialize(
                    path=TERMINAL_PATH
                )

                if not initialized:

                    print("MT5 INIT FAILED")

                    account.verification_status = "FAILED"

                    db.commit()

                    continue

                authorized = mt5.login(
                    login=int(account.login),
                    password=account.password,
                    server=account.server
                )

                if not authorized:

                    print("LOGIN FAILED")

                    print(mt5.last_error())

                    account.verification_status = "FAILED"

                    db.commit()

                    continue

                info = mt5.account_info()

                if not info:

                    print("ACCOUNT INFO FAILED")

                    account.verification_status = "FAILED"

                    db.commit()

                    continue

                # =========================
                # VERIFIED
                # =========================

                account.is_verified = True

                account.verification_status = "VERIFIED"

                account.account_name = info.name

                account.broker_name = info.company

                account.balance = info.balance

                account.equity = info.equity

                account.last_verified_at = datetime.utcnow()

                db.commit()

                print("VERIFIED SUCCESSFULLY")

                print(f"NAME: {info.name}")

                print(f"BROKER: {info.company}")

                print(f"BALANCE: {info.balance}")

                mt5.shutdown()

            except Exception as e:

                print("ACCOUNT ERROR")

                print(e)

        db.close()

    except Exception as e:

        print("WORKER ERROR")

        print(e)

    time.sleep(10)