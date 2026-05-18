import logging

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.database import get_db
from app.models import (
    AdminProfile,
    ClientActivation,
    ClientMT5Account,
    ClientSymbolSetting,
    EASymbol,
    ExpertAdvisor,
    License,
    TradeExecution,
)
from app.schemas import (
    ClientActivateRequest,
    ClientActivateResponse,
    ClientLicenseRequest,
    ClientMT5Response,
    ClientMT5SaveRequest,
    ClientMT5StatusRequest,
    ClientMT5StatusResponse,
    ClientSymbolSettingOut,
    ClientSymbolSettingSave,
    ClientRemoveSymbolRequest,
    ClientTradeHistoryRequest,
    ClientTradeHistoryItem,
    ClientRobotControlRequest,
    ClientRobotControlResponse,
)
from app.security import encrypt_text, decrypt_text
from app.services.metaapi_service import MetaApiService

from app.models import LiveTrade
from app.ai.models.ai_trade_history import AITradeHistory

router = APIRouter(prefix="/client", tags=["Client"])
logger = logging.getLogger(__name__)


# =========================
# DEPENDENCIES
# =========================
# In app/routers/client.py

async def get_metaapi_service() -> MetaApiService:
    """Async dependency for MetaApiService"""
    service = MetaApiService()
    await service.initialize()          # ← Important
    return service


# =========================
# HELPERS
# =========================
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def ensure_license_is_valid(license_row: Optional[License]) -> License:
    if not license_row:
        raise HTTPException(status_code=404, detail="Invalid license key")

    if not license_row.is_active:
        raise HTTPException(status_code=403, detail="License is deactivated")

    if license_row.expires_at:
        expires_at = license_row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < utc_now():
            raise HTTPException(status_code=403, detail="License has expired")

    return license_row


def get_license_by_key(license_key: str, db: Session) -> License:
    """Centralized license retrieval + validation"""
    license_row = db.query(License).filter(
        License.license_key == license_key.strip()
    ).first()
    return ensure_license_is_valid(license_row)


def build_mt5_response(message: str, license_row: License, row: ClientMT5Account) -> ClientMT5Response:
    return ClientMT5Response(
        message=message,
        license_key=license_row.license_key,
        mt_login=row.mt_login,
        mt_server=row.mt_server,
        is_active=row.is_active,
        verified=row.is_verified,
        account_name=row.account_name,
        broker_name=row.broker_name,
        balance=row.balance,
        equity=row.equity,
        last_verified_at=row.last_verified_at,
    )


def build_mt5_status_response(
    license_row: License,
    row: Optional[ClientMT5Account] = None,
    status: str = "not_connected",
    message: str = "No verified MT5 account connected",
) -> ClientMT5StatusResponse:
    if not row:
        return ClientMT5StatusResponse(
            license_key=license_row.license_key,
            mt_login=None,
            mt_server=None,
            is_active=False,
            verified=False,
            account_name=None,
            broker_name=None,
            balance=None,
            equity=None,
            last_verified_at=None,
            status=status,
            message=message,
        )

    return ClientMT5StatusResponse(
        license_key=license_row.license_key,
        mt_login=row.mt_login,
        mt_server=row.mt_server,
        is_active=row.is_active,
        verified=row.is_verified,
        account_name=row.account_name,
        broker_name=row.broker_name,
        balance=row.balance,
        equity=row.equity,
        last_verified_at=row.last_verified_at,
        status=status,
        message=message,
    )


def build_symbol_setting_response(row: ClientSymbolSetting) -> ClientSymbolSettingOut:
    return ClientSymbolSettingOut(
        id=row.id,
        symbol_name=row.symbol_name,
        trade_direction=row.trade_direction,
        lot_size=row.lot_size,
        max_open_trades=row.max_open_trades,
        trades_per_signal=row.trades_per_signal or 1,
        enabled=row.enabled,
    )


def build_trade_history_item(row: TradeExecution) -> ClientTradeHistoryItem:
    return ClientTradeHistoryItem(
        id=row.id,
        symbol=row.symbol,
        action=row.action,
        event_type=row.event_type,
        status=row.status,
        lot_size=row.lot_size,
        price=row.price,
        sl=row.sl,
        tp=row.tp,
        comment=row.comment,
        error_message=row.error_message,
        client_ticket=row.client_ticket,
        master_ticket=row.master_ticket,
        created_at=row.created_at,
    )


# =========================
# LICENSE ACTIVATION
# =========================
@router.post("/activate", response_model=ClientActivateResponse)
def activate_client_license(
    payload: ClientActivateRequest,
    db: Session = Depends(get_db)
):
    license_row = get_license_by_key(payload.license_key, db)

    if license_row.execution_enabled is None:
        license_row.execution_enabled = False

    ea = db.query(ExpertAdvisor).filter(ExpertAdvisor.id == license_row.ea_id).first()
    if not ea:
        raise HTTPException(status_code=404, detail="EA not found for license")

    profile = db.query(AdminProfile).filter(
        AdminProfile.admin_id == license_row.admin_id
    ).first()

    now = utc_now()
    device_id = payload.device_id.strip()
    device_name = payload.device_name.strip() if payload.device_name else None

    if not license_row.activated_device_id:
        license_row.activated_device_id = device_id
        license_row.activated_device_name = device_name
        license_row.first_activated_at = now
    elif license_row.activated_device_id != device_id:
        raise HTTPException(status_code=403, detail="License key already used on another device")

    license_row.activated_device_name = device_name
    license_row.last_seen_at = now

    if not db.query(ClientActivation).filter(ClientActivation.license_id == license_row.id).first():
        db.add(ClientActivation(
            license_id=license_row.id,
            activated=True,
            activated_at=now,
        ))

    db.commit()
    db.refresh(license_row)

    branding = {
        "display_name": profile.display_name if profile else None,
        "company_name": profile.company_name if profile else None,
        "logo_url": profile.logo_url if profile else None,
        "support_email": profile.support_email if profile else None,
        "telegram": profile.telegram if profile else None,
        "whatsapp": profile.whatsapp if profile else None,
    }

    return ClientActivateResponse(
        message="License activated successfully",
        license_key=license_row.license_key,
        client_name=license_row.client_name,
        client_email=license_row.client_email,
        mode_type=license_row.mode_type,
        expires_at=license_row.expires_at,
        ea_name=ea.name,
        ea_code_name=ea.code_name,
        branding=branding,
        activated_device_id=license_row.activated_device_id,
        activated_device_name=license_row.activated_device_name,
    )


# =========================
# MT5 ACCOUNT
# =========================
@router.post("/mt5/save", response_model=ClientMT5Response)
async def save_client_mt5(
    payload: ClientMT5SaveRequest,
    db: Session = Depends(get_db),
    metaapi_service: MetaApiService = Depends(get_metaapi_service),
):
    license_row = get_license_by_key(payload.license_key, db)

    # Get or create MT5 account record
    row = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()

    if row:
        # Update existing credentials
        row.mt_login = payload.mt_login.strip()
        row.mt_password = encrypt_text(payload.mt_password)
        row.mt_server = payload.mt_server.strip()
        row.is_verified = False
        row.is_active = False
        row.verification_error = None
    else:
        # Create new record
        row = ClientMT5Account(
            license_id=license_row.id,
            mt_login=payload.mt_login.strip(),
            mt_password=encrypt_text(payload.mt_password),
            mt_server=payload.mt_server.strip(),
            is_active=False,
            is_verified=False,
        )
        db.add(row)

    db.commit()
    db.refresh(row)

    try:
        # Create MetaAPI account if it doesn't exist
        if not row.metaapi_account_id:
            account = await metaapi_service.create_mt5_account(
                login=payload.mt_login,
                password=payload.mt_password,
                server=payload.mt_server,
                name=f"Nolimitz-{license_row.license_key[:12]}"
            )
            row.metaapi_account_id = account.id
            db.commit()
            db.refresh(row)
        else:
            try:
                account = await metaapi_service.get_account(row.metaapi_account_id)

            except Exception:
                logger.warning(
                    f"MetaApi account missing. Recreating for {license_row.license_key}"
                )

                account = await metaapi_service.create_mt5_account(
                    login=payload.mt_login,
                    password=payload.mt_password,
                    server=payload.mt_server,
                    name=f"Nolimitz-{license_row.license_key[:12]}"
                )

                row.metaapi_account_id = account.id
                db.commit()
                db.refresh(row)

        # === IMPROVED DEPLOYMENT ===
        logger.info(
            f"MetaApi state before deploy: {getattr(account, 'state', 'UNKNOWN')}"
        )

        logger.info(
            f"Deploying account {row.metaapi_account_id}..."
        )

        await metaapi_service.deploy_account_and_wait(
            account,
            timeout_seconds=300
        )

        logger.info(
            f"MetaApi deployment successful: {row.metaapi_account_id}"
        )

        # Get account info
        result = await metaapi_service.get_account_info(
            row.metaapi_account_id
        )
        info = result.get("info") or {}

        # Update success state
        row.is_verified = True
        row.is_active = True
        row.account_name = info.get("name")
        row.broker_name = info.get("broker")
        row.balance = info.get("balance")
        row.equity = info.get("equity")
        row.last_verified_at = utc_now()
        row.verification_error = None

        db.commit()
        db.refresh(row)

        logger.info(f"MT5 Account {row.mt_login} successfully connected for license {license_row.license_key}")
        
        return build_mt5_response(
            "MT5 connected successfully", 
            license_row, 
            row
        )

    except Exception as e:
        error_msg = str(e)[:500]
        logger.error(f"MT5 connection failed for license {license_row.license_key}: {error_msg}")

        row.is_verified = False
        row.is_active = False
        row.verification_error = error_msg
        row.last_verified_at = utc_now()
        db.commit()

        raise HTTPException(
            status_code=400, 
            detail=f"Failed to connect to broker: {error_msg}"
        )


@router.post("/mt5/status", response_model=ClientMT5StatusResponse)
def client_mt5_status(
    payload: ClientMT5StatusRequest,
    db: Session = Depends(get_db)
):
    license_row = get_license_by_key(payload.license_key, db)
    row = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()

    if not row:
        return build_mt5_status_response(license_row, status="not_connected")
    if row.is_verified:
        return build_mt5_status_response(license_row, row, "connected", "MT5 account connected successfully")
    if row.verification_error:
        return build_mt5_status_response(license_row, row, "failed", row.verification_error)

    return build_mt5_status_response(license_row, row, "pending", "MT5 verification pending")


# =========================
# TRADE HISTORY
# =========================
@router.post("/trade-history", response_model=list[ClientTradeHistoryItem])
def get_client_trade_history(
    payload: ClientTradeHistoryRequest,
    db: Session = Depends(get_db)
):
    license_row = get_license_by_key(payload.license_key, db)
    limit = min(payload.limit or 30, 100)

    rows = db.query(TradeExecution).filter(
        TradeExecution.license_id == license_row.id
    ).order_by(TradeExecution.id.desc()).limit(limit).all()

    return [build_trade_history_item(row) for row in rows]


# =========================
# SYMBOL SETTINGS
# =========================
@router.post("/symbols/save", response_model=ClientSymbolSettingOut)
def save_client_symbol_setting(
    payload: ClientSymbolSettingSave, db: Session = Depends(get_db)
):
    license_row = get_license_by_key(payload.license_key, db)

    ea = db.query(ExpertAdvisor).filter(ExpertAdvisor.id == license_row.ea_id).first()
    if not ea:
        raise HTTPException(status_code=404, detail="EA not found")

    normalized_symbol = normalize_symbol(payload.symbol_name)
    direction = payload.trade_direction.strip().lower()

    if direction not in ["buy", "sell", "both"]:
        raise HTTPException(status_code=400, detail="trade_direction must be buy, sell, or both")

    if payload.max_open_trades < 1 or payload.trades_per_signal < 1:
        raise HTTPException(status_code=400, detail="max_open_trades and trades_per_signal must be at least 1")

    if not db.query(EASymbol).filter(
        EASymbol.ea_id == ea.id,
        EASymbol.symbol_name == normalized_symbol,
        EASymbol.enabled == True,
    ).first():
        raise HTTPException(status_code=403, detail="Symbol is not allowed for this EA")

    existing = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == license_row.id,
        ClientSymbolSetting.symbol_name == normalized_symbol,
    ).first()

    if existing:
        existing.trade_direction = direction
        existing.lot_size = payload.lot_size
        existing.max_open_trades = payload.max_open_trades
        existing.trades_per_signal = payload.trades_per_signal
        existing.enabled = payload.enabled
        db.commit()
        db.refresh(existing)
        return build_symbol_setting_response(existing)

    new_row = ClientSymbolSetting(
        license_id=license_row.id,
        symbol_name=normalized_symbol,
        trade_direction=direction,
        lot_size=payload.lot_size,
        max_open_trades=payload.max_open_trades,
        trades_per_signal=payload.trades_per_signal,
        enabled=payload.enabled,
    )
    db.add(new_row)
    db.commit()
    db.refresh(new_row)
    return build_symbol_setting_response(new_row)


@router.post("/symbols/list", response_model=list[ClientSymbolSettingOut])
def list_client_symbol_settings(payload: ClientLicenseRequest, db: Session = Depends(get_db)):
    license_row = get_license_by_key(payload.license_key, db)

    rows = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == license_row.id,
        ClientSymbolSetting.enabled == True,
    ).order_by(ClientSymbolSetting.id.desc()).all()

    return [build_symbol_setting_response(row) for row in rows]


@router.post("/symbols/allowed")
def get_allowed_symbols(payload: ClientLicenseRequest, db: Session = Depends(get_db)):
    license_row = get_license_by_key(payload.license_key, db)
    ea = db.query(ExpertAdvisor).filter(ExpertAdvisor.id == license_row.ea_id).first()

    symbols = db.query(EASymbol).filter(
        EASymbol.ea_id == ea.id,
        EASymbol.enabled == True,
    ).order_by(EASymbol.id.asc()).all()

    return {
        "license_key": license_row.license_key,
        "ea_name": ea.name,
        "mode_type": license_row.mode_type,
        "allowed_symbols": [s.symbol_name for s in symbols],
    }


@router.post("/symbols/remove")
def remove_client_symbol_setting(payload: ClientRemoveSymbolRequest, db: Session = Depends(get_db)):
    license_row = get_license_by_key(payload.license_key, db)
    normalized = normalize_symbol(payload.symbol_name)

    row = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == license_row.id,
        ClientSymbolSetting.symbol_name == normalized,
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="Symbol setting not found")

    row.enabled = False
    db.commit()

    return {"message": "Symbol removed successfully", "symbol_name": normalized, "enabled": False}


# =========================
# ROBOT CONTROL
# =========================
@router.post("/robot/start", response_model=ClientRobotControlResponse)
def start_client_robot(payload: ClientRobotControlRequest, db: Session = Depends(get_db)):
    license_row = get_license_by_key(payload.license_key, db)

    mt5 = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()

    if not mt5 or not mt5.is_verified:
        raise HTTPException(status_code=400, detail="No verified MT5 account connected")

    license_row.execution_enabled = True
    license_row.execution_started_at = utc_now()
    license_row.last_seen_at = utc_now()
    db.commit()
    db.refresh(license_row)

    return ClientRobotControlResponse(
        message="Robot started successfully",
        license_key=license_row.license_key,
        execution_enabled=True,
        execution_started_at=license_row.execution_started_at,
    )


@router.post("/robot/stop", response_model=ClientRobotControlResponse)
def stop_client_robot(payload: ClientRobotControlRequest, db: Session = Depends(get_db)):
    license_row = get_license_by_key(payload.license_key, db)

    license_row.execution_enabled = False
    license_row.last_seen_at = utc_now()
    db.commit()
    db.refresh(license_row)

    return ClientRobotControlResponse(
        message="Robot stopped successfully",
        license_key=license_row.license_key,
        execution_enabled=False,
        execution_started_at=license_row.execution_started_at,
    )


# =========================
# WORKER ENDPOINTS
# =========================
@router.get("/worker/pending-mt5")
def worker_pending_mt5(db: Session = Depends(get_db)):
    rows = db.query(ClientMT5Account).filter(ClientMT5Account.is_verified == False).all()
    result = []
    for row in rows:
        license_row = db.get(License, row.license_id)
        if license_row:
            result.append({
                "license_key": license_row.license_key,
                "mt_login": row.mt_login,
                "mt_password": decrypt_text(row.mt_password),
                "mt_server": row.mt_server,
            })
    return result


@router.post("/worker/update-mt5-status")
def worker_update_mt5_status(payload: dict, db: Session = Depends(get_db)):
    license_key = payload.get("license_key")
    if not license_key:
        raise HTTPException(status_code=400, detail="license_key is required")

    license_row = db.query(License).filter(License.license_key == license_key).first()
    if not license_row:
        raise HTTPException(status_code=404, detail="License not found")

    row = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="MT5 account not found")

    row.is_verified = payload.get("verified", False)
    row.is_active = row.is_verified
    row.account_name = payload.get("account_name")
    row.broker_name = payload.get("broker_name")
    row.balance = payload.get("balance")
    row.equity = payload.get("equity")
    row.verification_error = payload.get("error")
    row.last_verified_at = utc_now()

    db.commit()
    return {"success": True}
