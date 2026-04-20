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
POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "0.7"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ========================= LOGGING =========================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ========================= HELPERS =========================
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
        "XAUUSD": ["XAUUSD", "XAUUSDM", "GOLD", "GOLDM", "XAUUSD.", "XAUUSDm"],
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
            for candidate in candidates:
                cu = candidate.upper()
                if us.startswith(cu) or us.endswith(cu) or cu in us:
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
            db.commit()

    except Exception as e:
        logger.warning("Failed to mark manual closes for license %s: %s", license_id, e)


# ======================= MAIN PROCESSING =======================
async def process_open_execution(db: Session, service: MetaApiService, trade: TradeExecution):
    try:
        license_row = get_license_row(db, trade.license_id)
        if not license_row:
            raise ValueError("License not found")

        account = get_active_account_for_license(db, trade.license_id)
        if not account or not account.metaapi_account_id:
            raise ValueError("No active and verified client MT5 account")

        await mark_manual_closes(db, service, account, trade.license_id)

        if not getattr(license_row, "execution_enabled", True):
            raise ValueError("Execution is disabled for this license")

        if getattr(license_row, "execution_started_at", None) and not is_same_or_after(
            trade.created_at,
            license_row.execution_started_at,
        ):
            raise ValueError("Trade is older than client's execution start time")

        setting = get_symbol_setting(db, trade.license_id, trade.symbol)
        if not setting:
            raise ValueError(f"Symbol {trade.symbol} is not enabled for this license")

        action = normalize_action(trade.action)
        direction = normalize_action(setting.trade_direction)

        if direction not in ["buy", "sell", "both"]:
            raise ValueError("Invalid trade direction setting")

        if direction != "both" and direction != action:
            raise ValueError(f"Trade direction '{action}' blocked by client setting")

        if has_open_map_for_master_ticket(db, trade.license_id, trade.master_ticket):
            raise ValueError("Master ticket already has an open mapped trade")

        current_open_count = count_open_mapped_trades(db, trade.license_id, trade.symbol)
        max_open = max(int(setting.max_open_trades or 1), 1)
        per_signal = max(int(setting.trades_per_signal or 1), 1)

        if current_open_count >= max_open:
            raise ValueError("Maximum open trades reached for this symbol")

        opens_to_make = min(per_signal, max_open - current_open_count)
        if opens_to_make <= 0:
            raise ValueError("No open slots available for this symbol")

        lot_size = clean_lot_size(setting.lot_size, 0.01)
        sl = clean_optional_price(trade.sl)
        tp = clean_optional_price(trade.tp)

        broker_symbol = await resolve_broker_symbol(
            service,
            account.metaapi_account_id,
            trade.symbol,
        )

        logger.info(
            "Execution prep | trade_id=%s | license_id=%s | requested_symbol=%s | broker_symbol=%s | action=%s | lot_size=%s | sl=%s | tp=%s",
            trade.id,
            trade.license_id,
            trade.symbol,
            broker_symbol,
            action,
            lot_size,
            sl,
            tp,
        )

        ea_name = None
        if getattr(trade, "ea", None) and getattr(trade.ea, "name", None):
            ea_name = trade.ea.name

        comment_text = (ea_name or trade.comment or "NolimitzBots")[:30]

        opened_count = 0
        last_client_ticket = None

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
                    logger.info("BUY RESULT | trade_id=%s | result=%s", trade.id, result)

                elif action == "sell":
                    result = await service.create_market_sell_order(
                        account_id=account.metaapi_account_id,
                        symbol=broker_symbol,
                        volume=lot_size,
                        stop_loss=sl,
                        take_profit=tp,
                        comment=comment_text,
                    )
                    logger.info("SELL RESULT | trade_id=%s | result=%s", trade.id, result)

                else:
                    raise ValueError(f"Unsupported action '{action}'")

                client_ticket = str(
                    result.get("positionId") or result.get("orderId") or result.get("id") or ""
                )

                if not client_ticket:
                    logger.warning("Created order but no ticket returned for trade %s", trade.id)

                last_client_ticket = client_ticket
                opened_count += 1

                map_row = TradeTicketMap(
                    license_id=trade.license_id,
                    execution_id=trade.id,
                    master_ticket=str(trade.master_ticket),
                    client_ticket=client_ticket,
                    symbol=normalize_symbol(trade.symbol),
                    is_closed=False,
                    closed_by_client=False,
                )
                db.add(map_row)
                db.commit()

            except Exception as e:
                logger.error("FULL EXECUTION ERROR | trade_id=%s | error=%s", trade.id, e)

                if hasattr(e, "details"):
                    logger.error(
                        "EXECUTION ERROR DETAILS | trade_id=%s | details=%s",
                        trade.id,
                        e.details,
                    )

                raise

        if opened_count <= 0:
            raise ValueError("No client trades were opened")

        trade.client_ticket = last_client_ticket
        trade.status = "executed"
        trade.error_message = None

    except Exception as e:
        trade.status = "failed"
        trade.error_message = str(e)

        if hasattr(e, "details"):
            logger.warning(
                "Open execution failed | trade_id=%s | error=%s | details=%s",
                trade.id,
                e,
                e.details,
            )
        else:
            logger.warning(
                "Open execution failed | trade_id=%s | error=%s",
                trade.id,
                e,
            )

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
        db: Session = SessionLocal()

        try:
            pending_trades = (
                db.query(TradeExecution)
                .filter(TradeExecution.status == "pending")
                .order_by(TradeExecution.id.asc())
                .limit(30)
                .all()
            )

            for trade in pending_trades:
                event_type = normalize_action(getattr(trade, "event_type", "open"))

                if event_type == "close":
                    await process_close_execution(db, service, trade)
                else:
                    await process_open_execution(db, service, trade)

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