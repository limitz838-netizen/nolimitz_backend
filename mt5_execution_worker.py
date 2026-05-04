from dotenv import load_dotenv
load_dotenv(dotenv_path=".env.local", override=True)

import asyncio
import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    ClientMT5Account,
    ClientSymbolSetting,
    License,
    TradeExecution,
    TradeTicketMap,
)
from app.services.metaapi_service import MetaApiService

# Reduce noisy third-party logs
logging.getLogger("metaapi_cloud_sdk").setLevel(logging.WARNING)
logging.getLogger("socketio").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)

# ========================= CONFIG =========================
POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "0.3"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ========================= LOGGING =========================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ========================= HELPERS =========================
def symbols_match(master_symbol: str, client_symbol: str) -> bool:
    m = normalize_symbol(master_symbol)
    c = normalize_symbol(client_symbol)

    if m == c:
        return True

    # Handles XAUUSD → XAUUSDc, EURUSD → EURUSD.m etc
    if c.startswith(m) or c.endswith(m) or m in c:
        return True

    return False

def utc_now():
    return datetime.now(timezone.utc)


def normalize_text(value) -> str:
    return str(value).strip() if value is not None else ""


def normalize_symbol(value) -> str:
    return normalize_text(value).upper()


def normalize_action(value) -> str:
    return normalize_text(value).lower()


def to_float(value, default=0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def clean_optional_price(value):
    try:
        if value is None or value == "":
            return None

        num = float(value)
        if num <= 0:
            return None

        return num
    except Exception:
        return None


def clean_lot_size(value, default=0.01):
    try:
        if value is None or value == "":
            return default

        num = float(value)
        if num <= 0:
            return default

        return num
    except Exception:
        return default


def is_same_or_after(left, right) -> bool:
    if left is None or right is None:
        return True

    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)

    return left >= right


async def resolve_broker_symbol(
    service: MetaApiService,
    account_id: str,
    requested_symbol: str,
) -> str:
    requested = normalize_symbol(requested_symbol)

    alias_map = {
        "XAUUSD": ["XAUUSD", "XAUUSDM", "GOLD", "GOLDM", "XAUUSD.", "XAUUSDm", "XAUUSDc"],
        "BTCUSD": ["BTCUSD", "BTCUSDM", "BTCUSDT", "BTCUSD.", "BTCUSDm"],
        "ETHUSD": ["ETHUSD", "ETHUSDM", "ETHUSDT", "ETHUSD.", "ETHUSDm"],
        "EURUSD": ["EURUSD", "EURUSDM", "EURUSD.", "EURUSDm"],
        "GBPUSD": ["GBPUSD", "GBPUSDM", "GBPUSD.", "GBPUSDm"],
        "USDJPY": ["USDJPY", "USDJPYM", "USDJPY.", "USDJPYm"],
    }

    candidates = alias_map.get(requested, [requested])

    try:
        symbols = await service.get_symbols(account_id) or []
        upper_map = {str(s).upper(): str(s) for s in symbols}

        for candidate in candidates:
            if candidate.upper() in upper_map:
                return upper_map[candidate.upper()]

        for s in symbols:
            us = str(s).upper()

            # remove common suffixes
            clean_us = us.replace(".", "").replace("M", "").replace("C", "")

            for candidate in candidates:
                cu = candidate.upper()

                clean_cu = cu.replace(".", "").replace("M", "").replace("C", "")

                if clean_cu in clean_us:
                    return str(s)

        return await service.find_broker_symbol(account_id, requested)
    except Exception as e:
        logger.error("Symbol resolution failed for %s: %s", requested_symbol, e)
        raise


def get_active_account_for_license(db: Session, license_id: int):
    return (
        db.query(ClientMT5Account)
        .filter(
            ClientMT5Account.license_id == license_id,
            ClientMT5Account.is_active.is_(True),
            ClientMT5Account.is_verified.is_(True),
        )
        .first()
    )


def get_symbol_setting(db: Session, license_id: int, symbol: str):
    return (
        db.query(ClientSymbolSetting)
        .filter(
            ClientSymbolSetting.license_id == license_id,
            ClientSymbolSetting.symbol_name == normalize_symbol(symbol),
            ClientSymbolSetting.enabled.is_(True),
        )
        .first()
    )


def get_license_row(db: Session, license_id: int):
    return db.query(License).filter(License.id == license_id).first()


def count_open_mapped_trades(db: Session, license_id: int, symbol: str) -> int:
    return (
        db.query(TradeTicketMap)
        .filter(
            TradeTicketMap.license_id == license_id,
            TradeTicketMap.symbol == normalize_symbol(symbol),
            TradeTicketMap.is_closed.is_(False),
        )
        .count()
    )


def has_open_map_for_master_ticket(db: Session, license_id: int, master_ticket: str) -> bool:
    return (
        db.query(TradeTicketMap)
        .filter(
            TradeTicketMap.license_id == license_id,
            TradeTicketMap.master_ticket == str(master_ticket),
            TradeTicketMap.is_closed.is_(False),
        )
        .first()
        is not None
    )


def get_open_maps_for_master_ticket(db: Session, license_id: int, master_ticket: str):
    return (
        db.query(TradeTicketMap)
        .filter(
            TradeTicketMap.license_id == license_id,
            TradeTicketMap.master_ticket == str(master_ticket),
            TradeTicketMap.is_closed.is_(False),
        )
        .all()
    )


async def mark_manual_closes(
    db: Session,
    service: MetaApiService,
    account: ClientMT5Account,
    license_id: int,
):
    open_maps = (
        db.query(TradeTicketMap)
        .filter(
            TradeTicketMap.license_id == license_id,
            TradeTicketMap.is_closed.is_(False),
        )
        .all()
    )

    if not open_maps:
        return

    try:
        positions = await service.get_positions(account.metaapi_account_id)
        live_ids = {str(p.get("id")) for p in positions if p.get("id") is not None}

        changed = False
        for row in open_maps:
            client_ticket = normalize_text(row.client_ticket)
            if client_ticket and client_ticket not in live_ids:
                row.is_closed = True
                row.closed_by_client = True
                row.closed_at = utc_now()
                changed = True

        if changed:
            db.flush()

    except Exception as e:
        logger.warning("Failed to mark manual closes for license %s: %s", license_id, e)


# ======================= MAIN PROCESSING =======================
async def process_open_execution(db: Session, service: MetaApiService, trade: TradeExecution):
    try:
        # ================= FAN-OUT =================
        if trade.license_id:
            # Normal (single user)
            licenses = [get_license_row(db, trade.license_id)]
        else:
            # MASTER SIGNAL → send to ALL users
            licenses = (
                db.query(License)
                .filter(License.is_active.is_(True))
                .all()
            )

        logger.info(f"FAN-OUT: Trade {trade.master_ticket} → {len(licenses)} users")

        total_opened = 0
        last_ticket = None

        # ================= LOOP USERS =================
        for license_row in licenses:
            if not license_row:
                continue

            try:
                license_id = license_row.id

                # 🔥 IMPORTANT: EA isolation
                if trade.ea_id != license_row.ea_id:
                    continue

                account = get_active_account_for_license(db, license_id)
                if not account or not account.metaapi_account_id:
                    continue

                await mark_manual_closes(db, service, account, license_id)

                if not getattr(license_row, "execution_enabled", True):
                    continue

                # 🔥 Late start protection
                start_time = getattr(license_row, "execution_started_at", None)

                if start_time:
                    trade_time = trade.created_at

                    if trade_time is None or trade_time < start_time:
                        logger.info(f"⏩ Skipping old trade for license {license_row.id}")
                        continue

                all_settings = (
                    db.query(ClientSymbolSetting)
                    .filter(
                        ClientSymbolSetting.license_id == license_id,
                        ClientSymbolSetting.enabled.is_(True),
                    )
                    .all()
                )

                setting = None

                for s in all_settings:
                    if symbols_match(trade.symbol, s.symbol_name):
                        setting = s
                        break

                if not setting:
                    logger.warning(f"No symbol match → master={trade.symbol} | license={license_id}")
                    continue

                action = normalize_action(trade.action)
                direction = normalize_action(setting.trade_direction)

                if direction not in ["buy", "sell", "both"]:
                    continue

                if direction != "both" and direction != action:
                    continue

                if has_open_map_for_master_ticket(db, license_id, trade.master_ticket):
                    continue

                current_open_count = count_open_mapped_trades(
                    db,
                    license_id,
                    setting.symbol_name,
                )

                trades_per_signal = int(setting.trades_per_signal or 1)

                if trades_per_signal < 1:
                   trades_per_signal = 1
                max_open_trades = int(setting.max_open_trades or 1)

                if max_open_trades < 1:
                    max_open_trades = 1

                remaining_slots = max_open_trades - current_open_count
                opens_to_make = min(trades_per_signal, max(0, remaining_slots))

                lot_size = float(setting.lot_size or 0.01)

                logger.info(
                    f"USER SETTINGS → license={license_id} | lot={lot_size} | per_signal={trades_per_signal} | max={max_open_trades} | current={current_open_count} | opening={opens_to_make}"
                )

                if opens_to_make <= 0:
                    continue


                if lot_size <= 0:
                    logger.warning(f"Invalid lot size for license {license_id}, defaulting to 0.01")
                    lot_size = 0.01
                sl = clean_optional_price(trade.sl)
                tp = clean_optional_price(trade.tp)

                broker_symbol = await resolve_broker_symbol(
                    service,
                    account.metaapi_account_id,
                    setting.symbol_name,  # 🔥 IMPORTANT
                )

                ea_name = None
                if getattr(trade, "ea", None) and getattr(trade.ea, "name", None):
                    ea_name = trade.ea.name

                comment_text = (ea_name or trade.comment or "NolimitzBots")[:30]

                opened_here = 0

                for _ in range(opens_to_make):
                    try:
                        if action == "buy":
                            result = await service.create_market_buy_order(
                                account_id=account.metaapi_account_id,
                                symbol=broker_symbol,
                                volume=lot_size,
                                stop_loss=sl,
                                take_profit=tp,
                                comment=comment_text,
                            )
                        else:
                            result = await service.create_market_sell_order(
                                account_id=account.metaapi_account_id,
                                symbol=broker_symbol,
                                volume=lot_size,
                                stop_loss=sl,
                                take_profit=tp,
                                comment=comment_text,
                            )

                        client_ticket = str(
                            result.get("positionId") or result.get("orderId") or result.get("id") or ""
                        )

                        last_ticket = client_ticket
                        opened_here += 1
                        total_opened += 1

                        db.add(TradeTicketMap(
                            license_id=license_id,
                            execution_id=trade.id,
                            master_ticket=str(trade.master_ticket),
                            client_ticket=client_ticket,
                            symbol=normalize_symbol(setting.symbol_name),
                            is_closed=False,
                            closed_by_client=False,
                        ))

                        db.commit()

                    except Exception as e:
                        logger.warning(f"Trade failed for license {license_id}: {e}")

                        trade.retry_count = (trade.retry_count or 0) + 1

                        if trade.retry_count <= 3:
                            trade.status = "retry"
                            trade.error_message = f"Retry {trade.retry_count}: {str(e)}"
                        else:
                            trade.status = "failed"
                            trade.error_message = f"Final failure after retries: {str(e)}"

                        db.commit()

                if opened_here > 0:
                    logger.info(f"Copied → license {license_id} ({opened_here} trades)")

            except Exception as e:
                logger.warning(f"License {license_row.id} skipped: {e}")

        # ================= FINAL RESULT =================
        if total_opened > 0:
            trade.status = "executed"
            trade.client_ticket = last_ticket
            trade.error_message = f"Copied to {total_opened} trades"
        else:
            trade.status = "failed"
            trade.error_message = "No users received trade"

    except Exception as e:
        trade.status = "failed"
        trade.error_message = str(e)
        logger.error(f"MASTER ERROR: {e}")

    finally:
        db.commit()


async def process_close_execution(db: Session, service: MetaApiService, trade: TradeExecution):
    try:
        account = get_active_account_for_license(db, trade.license_id)
        if not account or not account.metaapi_account_id:
            raise ValueError("No active client MT5 account for close")

        maps = get_open_maps_for_master_ticket(db, trade.license_id, trade.master_ticket)
        if not maps:
            raise ValueError("No open mapped trades found for master ticket")

        closed_any = 0

        for row in maps:
            client_ticket = normalize_text(row.client_ticket)

            if not client_ticket:
                row.is_closed = True
                row.closed_by_client = False
                row.closed_at = utc_now()
                db.commit()
                continue

            try:
                await service.close_position(
                    account_id=account.metaapi_account_id,
                    position_id=client_ticket,
                )
                row.is_closed = True
                row.closed_by_client = False
                row.closed_at = utc_now()
                row.last_error = None
                closed_any += 1
                db.commit()

            except Exception as e:
                logger.warning("Failed to close client ticket %s: %s", client_ticket, e)
                row.last_error = str(e)
                db.commit()

        if closed_any > 0:
            trade.status = "executed"
            trade.error_message = None
        else:
            trade.status = "failed"
            trade.error_message = "Failed to close any client positions"

    except Exception as e:
        trade.status = "failed"
        trade.error_message = str(e)
        logger.warning("Close execution failed for trade %s: %s", trade.id, e)

    finally:
        db.commit()


# ========================= WORKER =========================
async def run_worker():
    logger.info("MT5 Execution Worker started | Poll interval: %.2fs", POLL_SECONDS)
    service = MetaApiService()

    while True:
        db_factory = SessionLocal
        db = db_factory()

        try:
            pending_trades = (
                db.query(TradeExecution)
                .filter(
                    TradeExecution.status.in_(["pending", "retry"])
                    (TradeExecution.retry_count == None) | (TradeExecution.retry_count < 3)
                )
                .order_by(TradeExecution.id.asc())
                .limit(30)
                .all()
            )

            tasks = []

            for trade in pending_trades:
                event_type = normalize_action(getattr(trade, "event_type", "open"))

                if event_type == "close":
                    tasks.append(process_close_execution(db_factory(), service, trade))
                else:
                    tasks.append(process_open_execution(db_factory(), service, trade))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.error("Unexpected error in worker loop: %s", e, exc_info=True)

        finally:
            db.close()

        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.critical("Worker crashed: %s", e, exc_info=True)