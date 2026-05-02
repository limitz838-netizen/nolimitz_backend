from app.database import SessionLocal
from app.models import License, ClientMT5Account, ClientSymbolSetting
from datetime import datetime, timezone

db = SessionLocal()

licenses = db.query(License).all()

for lic in licenses:
    print(f"Processing license: {lic.license_key}")

    # ✅ Enable robot
    lic.execution_enabled = True
    lic.execution_started_at = datetime.now(timezone.utc)

    # ✅ Check MT5 exists
    mt5 = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == lic.id
    ).first()

    if not mt5:
        print(f"❌ No MT5 for {lic.license_key}")
        continue

    mt5.is_active = True
    mt5.is_verified = True

    # ✅ Ensure symbol exists (XAUUSD example)
    symbol = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == lic.id,
        ClientSymbolSetting.symbol_name == "XAUUSD"
    ).first()

    if not symbol:
        symbol = ClientSymbolSetting(
            license_id=lic.id,
            symbol_name="XAUUSD",
            trade_direction="both",
            lot_size=0.01,
            max_open_trades=5,
            trades_per_signal=1,
            enabled=True,
        )
        db.add(symbol)
        print(f"✅ Created symbol for {lic.license_key}")

    else:
        symbol.enabled = True

db.commit()
db.close()

print("🔥 ALL LICENSES ENABLED SUCCESSFULLY")