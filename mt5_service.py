import MetaTrader5 as mt5
import subprocess
import time


def verify_mt5_credentials_direct(
    mt_login,
    mt_password,
    mt_server,
    terminal_path=None,
):

    print("VERIFYING MT5:", {
        "login": mt_login,
        "server": mt_server,
        "terminal_path": terminal_path,
    })

    try:
        mt5.shutdown()
    except:
        pass

    # START TERMINAL FIRST
    if terminal_path:
        subprocess.Popen([terminal_path])

    # GIVE TERMINAL TIME TO OPEN
    time.sleep(8)

    # INITIALIZE
    if terminal_path:
        initialized = mt5.initialize(path=terminal_path)
    else:
        initialized = mt5.initialize()

    if not initialized:
        raise Exception(f"MT5 initialize failed: {mt5.last_error()}")

    # LOGIN
    authorized = mt5.login(
        login=int(mt_login),
        password=str(mt_password),
        server=str(mt_server),
    )

    print("AUTHORIZED:", authorized)

    if not authorized:
        error = mt5.last_error()
        mt5.shutdown()
        raise Exception(f"MT5 login failed: {error}")

    # WAIT
    time.sleep(8)

    account_info = mt5.account_info()

    if account_info is None:
        mt5.shutdown()
        raise Exception("Could not fetch account info")

    result = {
        "name": account_info.name,
        "login": account_info.login,
        "server": account_info.server,
        "broker_name": account_info.company,
        "balance": float(account_info.balance),
        "equity": float(account_info.equity),
    }

    print("MT5 VERIFIED:", result)

    mt5.shutdown()

    return result