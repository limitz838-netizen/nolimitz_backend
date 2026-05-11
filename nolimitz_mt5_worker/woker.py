import time
import requests
import MetaTrader5 as mt5

BACKEND = "https://nolimitz-backend-yfne.onrender.com"

while True:
    try:
        # get pending accounts
        r = requests.get(f"{BACKEND}/worker/pending-mt5")
        accounts = r.json()

        for acc in accounts:
            login = int(acc["mt_login"])
            password = acc["mt_password"]
            server = acc["mt_server"]
            license_key = acc["license_key"]

            mt5.shutdown()

            ok = mt5.initialize(
                login=login,
                password=password,
                server=server
            )

            if ok:
                info = mt5.account_info()

                requests.post(
                    f"{BACKEND}/worker/update-mt5-status",
                    json={
                        "license_key": license_key,
                        "verified": True,
                        "account_name": info.name,
                        "broker_name": info.server,
                        "balance": info.balance,
                        "equity": info.equity
                    }
                )

                mt5.shutdown()

            else:
                requests.post(
                    f"{BACKEND}/worker/update-mt5-status",
                    json={
                        "license_key": license_key,
                        "verified": False,
                        "failed": True,
                        "message": str(mt5.last_error())
                    }
                )

    except Exception as e:
        print("WORKER ERROR:", e)

    time.sleep(10)