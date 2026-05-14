import time
import MetaTrader5 as mt5

from app.database import SessionLocal
from app.models import AISignal
from app.models import Admin
from app.models import AISettings
from app.models import ClientMT5Account
from app.ai.models.ai_trade_history import AITradeHistory

# =========================
# MT5 LOGIN
# =========================

LOGIN = 105505352
PASSWORD = "qIQF2%Q~"
SERVER = "FBS-Demo"

# =========================
# CONNECT TO MT5
# =========================

if not mt5.initialize():
    print("MT5 initialize failed")
    quit()

authorized = mt5.login(
    LOGIN,
    password=PASSWORD,
    server=SERVER
)

if not authorized:
    print("MT5 login failed")
    quit()

print("AI Worker Connected To MT5")

# =========================
# PREVENT DUPLICATES
# =========================

db = SessionLocal()

latest_existing_signal = (
    db.query(AISignal)
    .order_by(AISignal.id.desc())
    .first()
)

if latest_existing_signal:
    last_signal_id = latest_existing_signal.id
else:
    last_signal_id = 0

db.close()

# =========================
# WORKER LOOP
# =========================

while True:

    try:

        db = SessionLocal()

        admin = db.query(Admin).first()

        if admin:

            if not admin.is_super_admin:

                if admin.plan == "FREE":

                    print("FREE PLAN - AUTO TRADING BLOCKED")

                    db.close()

                    time.sleep(5)

                    continue

        latest_signal = (
            db.query(AISignal)
            .order_by(AISignal.id.desc())
            .first()
        )

        if latest_signal:

            # =========================
            # AVOID DUPLICATE EXECUTION
            # =========================

            if latest_signal.id != last_signal_id:

                symbol = latest_signal.symbol
                signal = latest_signal.signal
                sl = latest_signal.stop_loss
                tp = latest_signal.take_profit

                # =========================
                # GET CONNECTED USERS
                # =========================

                connected_accounts = (
                    db.query(ClientMT5Account)
                    .filter(
                        ClientMT5Account.is_active == True,
                        ClientMT5Account.ai_auto_trade == True
                    )
                    .all()
                )

                print(
                    f"CONNECTED USERS: {len(connected_accounts)}"
                )

                print(f"\nNEW SIGNAL: {signal} {symbol}")

                # =========================
                # LOOP THROUGH USERS
                # =========================

                for account in connected_accounts:

                    print(
                        f"\nTRADING USER: {account.login}"
                    )

                    login = int(account.login)
                    password = account.password
                    server = account.server

                    mt5.shutdown()

                    mt5.initialize()

                    authorized = mt5.login(
                        login,
                        password=password,
                        server=server
                    )

                    if not authorized:

                        print(
                            f"FAILED LOGIN: {login}"
                        )

                        continue

                    print(
                        f"CONNECTED TO USER MT5: {login}"
                    )

                # =========================
                # CHECK SYMBOL
                # =========================

                symbol_info = mt5.symbol_info(symbol)

                if symbol_info is None:
                    print(f"{symbol} not found")
                    db.close()
                    time.sleep(5)
                    continue

                if not symbol_info.visible:
                    mt5.symbol_select(symbol, True)

                tick = mt5.symbol_info_tick(symbol)

                if tick is None:
                    print("Tick data not found")
                    db.close()
                    time.sleep(5)
                    continue

                # =========================
                # CHECK EXISTING POSITIONS
                # =========================

                positions = mt5.positions_get(symbol=symbol)

                user_total_positions = mt5.positions_get()

                if (
                    user_total_positions
                    and len(user_total_positions)
                    >= account.max_ai_trades
                ):

                    print(
                        "MAX TRADES REACHED"
                    )

                    continue

                if positions:

                    same_direction_exists = False

                    for pos in positions:

                        # BUY POSITION
                        if (
                            pos.type == mt5.POSITION_TYPE_BUY
                            and signal == "BUY"
                        ):
                            same_direction_exists = True

                        # SELL POSITION
                        elif (
                            pos.type == mt5.POSITION_TYPE_SELL
                            and signal == "SELL"
                        ):
                            same_direction_exists = True

                    if same_direction_exists:

                        print("Duplicate trade prevented")

                        last_signal_id = latest_signal.id

                        db.close()

                        time.sleep(5)

                        continue


                # =========================
                # CLOSE OPPOSITE POSITIONS
                # =========================

                for pos in positions:

                    # CLOSE BUY IF NEW SELL
                    if (
                        pos.type == mt5.POSITION_TYPE_BUY
                        and signal == "SELL"
                    ):

                        close_request = {
                            "action": mt5.TRADE_ACTION_DEAL,
                            "symbol": symbol,
                            "volume": pos.volume,
                            "type": mt5.ORDER_TYPE_SELL,
                            "position": pos.ticket,
                            "price": tick.bid,
                            "deviation": 20,
                            "magic": 777,
                            "comment": "Close Opposite Buy",
                            "type_time": mt5.ORDER_TIME_GTC,
                            "type_filling": mt5.ORDER_FILLING_IOC,
                        }

                        mt5.order_send(close_request)

                        print("Closed BUY position")

                    # CLOSE SELL IF NEW BUY
                    elif (
                       pos.type == mt5.POSITION_TYPE_SELL
                       and signal == "BUY"
                    ):

                        close_request = {
                            "action": mt5.TRADE_ACTION_DEAL,
                            "symbol": symbol,
                            "volume": pos.volume,
                            "type": mt5.ORDER_TYPE_BUY,
                            "position": pos.ticket,
                            "price": tick.ask,
                            "deviation": 20,
                            "magic": 777,
                            "comment": "Close Opposite Sell",
                            "type_time": mt5.ORDER_TIME_GTC,
                            "type_filling": mt5.ORDER_FILLING_IOC,
                        }

                        mt5.order_send(close_request)

                        print("Closed SELL position")

                # =========================
                # ORDER TYPE
                # =========================

                if signal == "BUY" and not account.allow_buy:

                    print("BUY DISABLED FOR USER")

                    continue

                if signal == "SELL" and not account.allow_sell:

                    print("SELL DISABLED FOR USER")

                    continue

                if signal == "BUY":
                    order_type = mt5.ORDER_TYPE_BUY
                    price = tick.ask

                elif signal == "SELL":
                    order_type = mt5.ORDER_TYPE_SELL
                    price = tick.bid

                else:
                    print("WAIT signal skipped")
                    db.close()
                    time.sleep(5)
                    continue

                settings = db.query(AISettings).first()

                if not settings:

                    settings = AISettings(
                        auto_lot=True,
                        fixed_lot=0.01,
                        risk_percent=2,
                        max_trades=1,
                        aggressive_mode=False
                    )

                    db.add(settings)

                    db.commit()

                    db.refresh(settings)

                lot_size = settings.fixed_lot

                if settings.auto_lot:

                    account_info = mt5.account_info()

                    balance = account_info.balance

                    risk_percent = account.risk_percent

                    lot_size = round(
                        (balance * risk_percent / 100) / 1000,
                        2
                    )

                    if lot_size < 0.01:
                        lot_size = 0.01

                # =========================
                # TRADE REQUEST
                # =========================

                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": 0.01,
                    "type": order_type,
                    "price": price,
                    "sl": sl,
                    "tp": tp,
                    "deviation": 20,
                    "magic": 777,
                    "comment": "Nolimitz AI",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }

                print("\nSENDING ORDER...")
                print(request)

                # =========================
                # SEND ORDER
                # =========================

                result = mt5.order_send(request)

                # =========================
                # CHECK RESULT
                # =========================

                if result is None:

                    print("\nORDER FAILED")
                    print(mt5.last_error())

                else:

                    print("\nTRADE RESULT:")
                    print(result)

                    if result.retcode == mt5.TRADE_RETCODE_DONE:

                        print("\nTRADE OPENED SUCCESSFULLY")

                    else:

                        print("\nTRADE FAILED")
                        print("Retcode:", result.retcode)

                # =========================
                # SAVE TRADE HISTORY
                # =========================

                trade_history = AITradeHistory(

                    symbol=symbol,

                    signal=signal,

                    trend=latest_signal.trend,

                    entry_price=price,

                    stop_loss=sl,

                    take_profit=tp,

                    confidence=latest_signal.confidence,

                    result="OPEN",

                    profit=0
                )

                db.add(trade_history)

                db.commit()

                print(
                    "TRADE SAVED TO HISTORY"
                )      

                # =========================
                # MARK AS EXECUTED
                # =========================

                last_signal_id = latest_signal.id

        db.close()

    except Exception as e:

        print("\nWORKER ERROR:")
        print(e)

    time.sleep(5)