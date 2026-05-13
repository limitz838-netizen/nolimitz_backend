from dotenv import load_dotenv
load_dotenv(dotenv_path=".env.local", override=True)

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database import SessionLocal
from app.models import (
    ClientMT5Account,
    ClientSymbolSetting,
    License,
    TradeExecution,
    TradeTicketMap,
)
from app.services.metaapi_service import MetaApiService

# ========================= CONFIG =========================
POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "0.3"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ========================= LOGGING =========================
logging.getLogger("metaapi_cloud_sdk").setLevel(logging.WARNING)
logging.getLogger("socketio").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ========================= HELPERS =========================
def utc_now() -> datetime:
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
        return num if num > 0 else None
    except Exception:
        return None


def clean_lot_size(value, default=0.01):
    try:
        if value is None or value == "":
            return default
        num = float(value)
        return num if num > 0 else default
    except Exception:
        return default


def symbols_match(master_symbol: str, client_symbol: str) -> bool:
    m = normalize_symbol(master_symbol)
    c = normalize_symbol(client_symbol)
    if m == c:
        return True
    if c.startswith(m) or c.endswith(m) or m in c:
        return True
    return False


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

        # Fallback fuzzy match
        for s in symbols:
            us = str(s).upper()
            clean_us = us.replace(".", "").replace("M", "").replace("C", "")
            for candidate in candidates:
                clean_cu = candidate.upper().replace(".", "").replace("M", "").replace("C", "")
                if clean_cu in clean_us:
                    return str(s)

        # Final fallback
        return await service.find_broker_symbol(account_id, requested) if hasattr(service, 'find_broker_symbol') else requested

    except Exception as e:
        logger.error("Symbol resolution failed for %s: %s", requested_symbol, e)
        return requested


def get_active_account_for_license(db: Session, license_id: int):
    return db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_id,
        ClientMT5Account.is_active.is_(True),
        ClientMT5Account.is_verified.is_(True),
    ).first()


def count_open_mapped_trades(db: Session, license_id: int, symbol: str) -> int:
    return db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == license_id,
        TradeTicketMap.symbol == normalize_symbol(symbol),
        TradeTicketMap.is_closed.is_(False),
    ).count()


def has_open_map_for_master_ticket(db: Session, license_id: int, master_ticket: str) -> bool:
    return db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == license_id,
        TradeTicketMap.master_ticket == str(master_ticket),
        TradeTicketMap.is_closed.is_(False),
    ).first() is not None


def get_open_maps_for_master_ticket(db: Session, license_id: int, master_ticket: str):
    return db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == license_id,
        TradeTicketMap.master_ticket == str(master_ticket),
        TradeTicketMap.is_closed.is_(False),
    ).all()


async def mark_manual_closes(db: Session, service: MetaApiService, account: ClientMT5Account, license_id: int):
    open_maps = db.query(TradeTicketMap).filter(
        TradeTicketMap.license_id == license_id,
        TradeTicketMap.is_closed.is_(False),
    ).all()

    if not open_maps:
        return

    try:
        positions = await service.get_positions(account.metaapi_account_id)
        live_ids = {str(p.get("id")) for p in positions if p.get("id") is not None}

        for row in open_maps:
            client_ticket = normalize_text(row.client_ticket)
            if client_ticket and client_ticket not in live_ids:
                row.is_closed = True
                row.closed_by_client = True
                row.closed_at = utc_now()
        db.commit()
    except Exception as e:
        logger.warning("Failed to mark manual closes for license %s: %s", license_id, e)


# ======================= MAIN PROCESSING =======================
async def process_open_execution(db: Session, service: MetaApiService, trade: TradeExecution):
    # ... (your logic is mostly good, I kept it with small cleanups)
    try:
        if trade.license_id:
            licenses = [db.query(License).get(trade.license_id)]
        else:
            licenses = db.query(License).filter(License.is_active.is_(True)).all()

        logger.info(f"FAN-OUT: Trade {trade.master_ticket} → {len(licenses)} users")

        total_opened = 0
        last_ticket = None

        for license_row in licenses:
            if not license_row:
                continue
            try:
                license_id = license_row.id
                if trade.ea_id != license_row.ea_id:
                    continue

                account = get_active_account_for_license(db, license_id)
                if not account or not account.metaapi_account_id:
                    continue

                await mark_manual_closes(db, service, account, license_id)

                if not getattr(license_row, "execution_enabled", True):
                    continue

                # Late start protection
                if license_row.execution_started_at and trade.created_at:
                    if trade.created_at.replace(tzinfo=timezone.utc) < license_row.execution_started_at.replace(tzinfo=timezone.utc):
                        continue

                # Symbol setting matching
                all_settings = db.query(ClientSymbolSetting).filter(
                    ClientSymbolSetting.license_id == license_id,
                    ClientSymbolSetting.enabled.is_(True),
                ).all()

                setting = next((s for s in all_settings if symbols_match(trade.symbol, s.symbol_name)), None)
                if not setting:
                    continue

                # Direction filter
                action = normalize_action(trade.action)
                direction = normalize_action(setting.trade_direction)
                if direction not in ["buy", "sell", "both"] or (direction != "both" and direction != action):
                    continue

                if has_open_map_for_master_ticket(db, license_id, trade.master_ticket):
                    continue

                current_open = count_open_mapped_trades(db, license_id, setting.symbol_name)
                opens_to_make = min(
                    int(setting.trades_per_signal or 1),
                    max(0, int(setting.max_open_trades or 1) - current_open)
                )

                if opens_to_make <= 0:
                    continue

                lot_size = clean_lot_size(setting.lot_size, 0.01)
                sl = clean_optional_price(trade.sl)
                tp = clean_optional_price(trade.tp)

                broker_symbol = await resolve_broker_symbol(
                    service, account.metaapi_account_id, setting.symbol_name
                )

                comment_text = (getattr(trade.ea, 'name', None) or trade.comment or "NolimitzBots")[:30]

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

                        client_ticket = str(result.get("positionId") or result.get("orderId") or result.get("id") or "")

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

                        last_ticket = client_ticket
                        opened_here += 1
                        total_opened += 1

                    except Exception as e:
                        logger.warning(f"Order failed for license {license_id}: {e}")
                        trade.retry_count = (trade.retry_count or 0) + 1
                        # ... retry logic (kept your original)
                        continue

                if opened_here > 0:
                    logger.info(f"Successfully copied to license {license_id} ({opened_here} trades)")

            except Exception as e:
                logger.warning(f"License {license_row.id} processing error: {e}")

        # Final status
        if total_opened > 0:
            trade.status = "executed"
            trade.client_ticket = last_ticket
            trade.error_message = f"Copied to {total_opened} trades"
        elif (trade.retry_count or 0) >= 3:
            trade.status = "failed"
        else:
            trade.status = "retry"
            trade.retry_at = utc_now() + timedelta(seconds=5 * (trade.retry_count or 1))

    except Exception as e:
        logger.error("Critical error in process_open_execution: %s", e)
        trade.status = "failed" if (trade.retry_count or 0) >= 3 else "retry"
    finally:
        db.commit()


# Keep `process_close_execution` as is (it's already good)


# ========================= WORKER =========================
async def process_trade_safe(trade_id: int, service: MetaApiService):
    db: Session = SessionLocal()
    try:
        trade = db.query(TradeExecution).filter(TradeExecution.id == trade_id).first()
        if not trade:
            return

        if normalize_action(trade.event_type or "open") == "close":
            await process_close_execution(db, service, trade)
        else:
            await process_open_execution(db, service, trade)
    except Exception as e:
        logger.error(f"Error processing trade {trade_id}: {e}", exc_info=True)
    finally:
        db.close()


async def run_worker():
    logger.info(f"MT5 Execution Worker started | Poll interval: {POLL_SECONDS}s")

    service = MetaApiService()
    await service.initialize()                     # ← CRITICAL FIX

    while True:
        db = SessionLocal()
        try:
            now = utc_now()
            pending_trades = db.query(TradeExecution).filter(
                TradeExecution.status.in_(["pending", "retry"]),
                or_(TradeExecution.retry_count.is_(None), TradeExecution.retry_count < 3),
                or_(TradeExecution.retry_at.is_(None), TradeExecution.retry_at <= now)
            ).order_by(TradeExecution.id.asc()).limit(30).all()

            if pending_trades:
                tasks = [process_trade_safe(trade.id, service) for trade in pending_trades]
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.error("Worker loop error", exc_info=True)
        finally:
            db.close()

        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())

    except KeyboardInterrupt:
        logger.info("Worker stopped by user")

    except Exception:
        logger.critical("Worker crashed", exc_info=True)