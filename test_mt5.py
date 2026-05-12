import MetaTrader5 as mt5
import time

TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

LOGIN = 12345678
PASSWORD = "YOUR_PASSWORD"
SERVER = "YOUR_SERVER"

print("STARTING MT5 TEST")

mt5.shutdown()

initialized = mt5.initialize(path=TERMINAL_PATH)

print("INITIALIZED:", initialized)

if not initialized:
    print("INIT ERROR:", mt5.last_error())
    quit()

authorized = mt5.login(
    login=161527062,
    password="Uthman7688@",
    server="ExnessKE-MT5Real21",
)

print("AUTHORIZED:", authorized)

if not authorized:
    print("LOGIN ERROR:", mt5.last_error())
    mt5.shutdown()
    quit()

time.sleep(3)

account = mt5.account_info()

print("ACCOUNT:", account)

mt5.shutdown()

print("DONE")