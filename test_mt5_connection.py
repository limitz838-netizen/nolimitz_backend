import MetaTrader5 as mt5
import sys

print("🔍 Nolimitz MT5 Connection Test\n")

# Test Masters
print("=== Testing Masters Folder ===")
if mt5.initialize(path=r"C:\NolimitzTerminals\Masters\terminal64.exe"):   # Change if your path is different
    account = mt5.account_info()
    if account:
        print(f"✅ Master Connected! Login: {account.login} | Balance: {account.balance}")
    else:
        print("⚠️ Connected but no account info")
    mt5.shutdown()
else:
    print("❌ Failed to connect to Masters terminal")

print("\n=== Testing Subscribers_Group1 ===")
if mt5.initialize(path=r"C:\NolimitzTerminals\Subscribers_Group1\terminal64.exe"):
    account = mt5.account_info()
    if account:
        print(f"✅ Group1 Connected! Login: {account.login}")
    mt5.shutdown()
else:
    print("❌ Failed to connect to Subscribers_Group1")

print("\nTest finished.")