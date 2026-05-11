import MetaTrader5 as mt5

LOGIN = 161527062
PASSWORD = "Uthman7688@"
SERVER = "ExnessKE-MT5Real21"

print("Connecting...")

connected = mt5.initialize(
    login=LOGIN,
    password=PASSWORD,
    server=SERVER,
)

print("CONNECTED:", connected)

if not connected:
    print("ERROR:", mt5.last_error())
else:
    print(mt5.account_info())

mt5.shutdown()