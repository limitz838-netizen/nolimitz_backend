from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

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
    ClientMT5ReverifyRequest,
    ClientMT5SaveRequest,
    ClientMT5StatusRequest,
    ClientMT5StatusResponse,
    ClientSymbolSettingOut,
    ClientSymbolSettingSave,
    ClientTradeHistoryRequest,
    ClientTradeHistoryItem,
)
from app.security import encrypt_text
from app.services.metaapi_service import MetaApiService

router = APIRouter(prefix="/client", tags=["Client"])


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

    expires_at = license_row.expires_at
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at < utc_now():
            raise HTTPException(status_code=403, detail="License has expired")

    return license_row


def get_license_by_key(license_key: str, db: Session) -> License:
    license_row = db.query(License).filter(
        License.license_key == license_key.strip()
    ).first()

    if not license_row:
        raise HTTPException(status_code=404, detail="Invalid license key")

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
    row: Optional[ClientMT5Account],
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
    license_row = db.query(License).filter(
        License.license_key == payload.license_key.strip()
    ).first()

    license_row = ensure_license_is_valid(license_row)

    ea = db.query(ExpertAdvisor).filter(
        ExpertAdvisor.id == license_row.ea_id
    ).first()
    if not ea:
        raise HTTPException(status_code=404, detail="EA not found for license")

    profile = db.query(AdminProfile).filter(
        AdminProfile.admin_id == license_row.admin_id
    ).first()

    now = utc_now()
    device_id = payload.device_id.strip()
    device_name = payload.device_name.strip() if payload.device_name else None

    # one-device lock
    if not license_row.activated_device_id:
        license_row.activated_device_id = device_id
        license_row.activated_device_name = device_name
        license_row.first_activated_at = now
        license_row.last_seen_at = now
        db.commit()
        db.refresh(license_row)

    elif license_row.activated_device_id == device_id:
        license_row.activated_device_name = device_name
        license_row.last_seen_at = now
        db.commit()
        db.refresh(license_row)

    else:
        raise HTTPException(
            status_code=403,
            detail="License key already used on another device",
        )

    existing_activation = db.query(ClientActivation).filter(
        ClientActivation.license_id == license_row.id
    ).first()

    if not existing_activation:
        activation = ClientActivation(
            license_id=license_row.id,
            activated=True,
            activated_at=now,
        )
        db.add(activation)
        db.commit()

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
# MT5
# =========================
@router.post("/mt5/save", response_model=ClientMT5Response)
async def save_client_mt5(
    payload: ClientMT5SaveRequest,
    db: Session = Depends(get_db)
):
    license_row = get_license_by_key(payload.license_key, db)

    existing = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()

    if existing:
        row = existing
        row.mt_login = payload.mt_login.strip()
        row.mt_password = encrypt_text(payload.mt_password)
        row.mt_server = payload.mt_server.strip()
    else:
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

    # Reset account state before reconnecting
    row.account_name = None
    row.broker_name = None
    row.balance = None
    row.equity = None
    row.last_verified_at = None
    row.last_sync_at = None
    row.verification_error = None
    row.is_verified = False
    row.is_active = False

    row.metaapi_account_id = None
    row.metaapi_state = None
    row.metaapi_connection_status = None
    row.metaapi_region = None
    row.metaapi_type = None
    row.metaapi_reliability = None

    db.commit()
    db.refresh(row)

    now = utc_now()
    service = MetaApiService()

    try:
        account = await service.create_mt5_account(
            login=row.mt_login,
            password=payload.mt_password.strip(),
            server=row.mt_server,
            name=f"Nolimitz-{license_row.license_key}"
        )

        await service.deploy_account_and_wait(account)

        result = await service.get_account_info(account.id)
        info = result["info"]
        account_obj = result["account"]

        row.metaapi_account_id = account.id
        row.metaapi_state = getattr(account_obj, "state", None)
        row.metaapi_connection_status = getattr(account_obj, "connection_status", None)
        row.metaapi_region = getattr(account_obj, "region", None)
        row.metaapi_type = getattr(account_obj, "type", "cloud-g2")
        row.metaapi_reliability = getattr(account_obj, "reliability", None)

        row.account_name = info.get("name")
        row.broker_name = info.get("broker")
        row.balance = info.get("balance")
        row.equity = info.get("equity")
        row.last_verified_at = now
        row.last_sync_at = now
        row.verification_error = None
        row.is_verified = True
        row.is_active = True

        db.commit()
        db.refresh(row)

        return build_mt5_response(
            "MT5 account connected successfully",
            license_row,
            row,
        )

    except Exception as e:
        row.account_name = None
        row.broker_name = None
        row.balance = None
        row.equity = None
        row.last_verified_at = now
        row.last_sync_at = now
        row.verification_error = str(e)
        row.is_verified = False
        row.is_active = False

        db.commit()
        db.refresh(row)

        raise HTTPException(
            status_code=400,
            detail=f"MT5 connection failed: {str(e)}",
        )


@router.post("/mt5/status", response_model=ClientMT5StatusResponse)
async def client_mt5_status(payload: ClientMT5StatusRequest, db: Session = Depends(get_db)):
    license_row = db.query(License).filter(
        License.license_key == payload.license_key.strip()
    ).first()

    if not license_row:
        raise HTTPException(status_code=404, detail="License not found")

    row = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()

    if not row:
        return build_mt5_status_response(
            license_row=license_row,
            row=None,
            status="not_connected",
            message="No MT5 account connected",
        )

    if not row.metaapi_account_id:
        if row.verification_error:
            return build_mt5_status_response(
                license_row=license_row,
                row=row,
                status="failed",
                message=row.verification_error,
            )

        return build_mt5_status_response(
            license_row=license_row,
            row=row,
            status="not_connected",
            message="No MetaApi account connected",
        )

    service = MetaApiService()

    try:
        result = await service.get_account_info(row.metaapi_account_id)
        info = result["info"]
        account_obj = result["account"]

        row.account_name = info.get("name")
        row.broker_name = info.get("broker")
        row.balance = info.get("balance")
        row.equity = info.get("equity")
        row.last_verified_at = utc_now()
        row.last_sync_at = utc_now()
        row.verification_error = None
        row.is_verified = True
        row.is_active = True

        row.metaapi_state = getattr(account_obj, "state", row.metaapi_state)
        row.metaapi_connection_status = getattr(account_obj, "connection_status", row.metaapi_connection_status)
        row.metaapi_region = getattr(account_obj, "region", row.metaapi_region)
        row.metaapi_type = getattr(account_obj, "type", row.metaapi_type)
        row.metaapi_reliability = getattr(account_obj, "reliability", row.metaapi_reliability)

        db.commit()
        db.refresh(row)

        return build_mt5_status_response(
            license_row=license_row,
            row=row,
            status="connected",
            message="MT5 account connected successfully",
        )

    except Exception as e:
        row.last_verified_at = utc_now()
        row.last_sync_at = utc_now()
        row.verification_error = str(e)
        row.is_verified = False
        row.is_active = False
        db.commit()
        db.refresh(row)

        return build_mt5_status_response(
            license_row=license_row,
            row=row,
            status="failed",
            message=str(e),
        )


@router.post("/mt5/reverify", response_model=ClientMT5Response)
async def reverify_client_mt5(payload: ClientMT5ReverifyRequest, db: Session = Depends(get_db)):
    license_row = get_license_by_key(payload.license_key, db)

    row = db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_row.id
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="No MT5 account saved for this license")

    if not row.metaapi_account_id:
        raise HTTPException(status_code=400, detail="No MetaApi account linked to this MT5 account")

    now = utc_now()
    service = MetaApiService()

    try:
        result = await service.get_account_info(row.metaapi_account_id)
        info = result["info"]
        account_obj = result["account"]

        row.account_name = info.get("name")
        row.broker_name = info.get("broker")
        row.balance = info.get("balance")
        row.equity = info.get("equity")
        row.last_verified_at = now
        row.last_sync_at = now
        row.verification_error = None
        row.is_verified = True
        row.is_active = True

        row.metaapi_state = getattr(account_obj, "state", row.metaapi_state)
        row.metaapi_connection_status = getattr(account_obj, "connection_status", row.metaapi_connection_status)
        row.metaapi_region = getattr(account_obj, "region", row.metaapi_region)
        row.metaapi_type = getattr(account_obj, "type", row.metaapi_type)
        row.metaapi_reliability = getattr(account_obj, "reliability", row.metaapi_reliability)

        db.commit()
        db.refresh(row)

        return build_mt5_response(
            "MT5 account reverified successfully",
            license_row,
            row,
        )

    except Exception as e:
        row.last_verified_at = now
        row.last_sync_at = now
        row.verification_error = f"MT5 reverify failed: {str(e)}"
        row.is_verified = False
        row.is_active = False

        db.commit()
        db.refresh(row)

        raise HTTPException(status_code=400, detail=f"MT5 reverify failed: {str(e)}")


@router.post("/trade-history", response_model=list[ClientTradeHistoryItem])
def get_client_trade_history(
    payload: ClientTradeHistoryRequest,
    db: Session = Depends(get_db),
):
    license_row = get_license_by_key(payload.license_key, db)

    limit = payload.limit if payload.limit and payload.limit > 0 else 30
    limit = min(limit, 100)

    rows = db.query(TradeExecution).filter(
        TradeExecution.license_id == license_row.id
    ).order_by(
        TradeExecution.id.desc()
    ).limit(limit).all()

    return [build_trade_history_item(row) for row in rows]

# =========================
# SYMBOL SETTINGS (unchanged)
# =========================
@router.post("/symbols/save", response_model=ClientSymbolSettingOut)
def save_client_symbol_setting(payload: ClientSymbolSettingSave, db: Session = Depends(get_db)):
    license_row = get_license_by_key(payload.license_key, db)
    license_row = ensure_license_is_valid(license_row)

    ea = db.query(ExpertAdvisor).filter(ExpertAdvisor.id == license_row.ea_id).first()
    if not ea:
        raise HTTPException(status_code=404, detail="EA linked to license not found")

    normalized_symbol = normalize_symbol(payload.symbol_name)
    normalized_direction = payload.trade_direction.strip().lower()

    if normalized_direction not in ["buy", "sell", "both"]:
        raise HTTPException(status_code=400, detail="trade_direction must be buy, sell, or both")

    if payload.max_open_trades < 1:
        raise HTTPException(status_code=400, detail="max_open_trades must be at least 1")

    if payload.trades_per_signal < 1:
        raise HTTPException(status_code=400, detail="trades_per_signal must be at least 1")

    allowed_symbol = db.query(EASymbol).filter(
        EASymbol.ea_id == ea.id,
        EASymbol.symbol_name == normalized_symbol,
        EASymbol.enabled == True,
    ).first()

    if not allowed_symbol:
        raise HTTPException(status_code=403, detail="Symbol is not allowed for this EA")

    existing = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == license_row.id,
        ClientSymbolSetting.symbol_name == normalized_symbol,
    ).first()

    if existing:
        existing.trade_direction = normalized_direction
        existing.lot_size = payload.lot_size
        existing.max_open_trades = payload.max_open_trades
        existing.trades_per_signal = payload.trades_per_signal
        existing.enabled = payload.enabled
        db.commit()
        db.refresh(existing)
        return build_symbol_setting_response(existing)

    row = ClientSymbolSetting(
        license_id=license_row.id,
        symbol_name=normalized_symbol,
        trade_direction=normalized_direction,
        lot_size=payload.lot_size,
        max_open_trades=payload.max_open_trades,
        trades_per_signal=payload.trades_per_signal,
        enabled=payload.enabled,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return build_symbol_setting_response(row)


@router.post("/symbols/list", response_model=list[ClientSymbolSettingOut])
def list_client_symbol_settings(payload: ClientLicenseRequest, db: Session = Depends(get_db)):
    license_row = get_license_by_key(payload.license_key, db)

    rows = db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == license_row.id
    ).order_by(ClientSymbolSetting.id.desc()).all()

    return [build_symbol_setting_response(row) for row in rows]


@router.post("/symbols/allowed")
def get_allowed_symbols(payload: ClientLicenseRequest, db: Session = Depends(get_db)):
    license_row = get_license_by_key(payload.license_key, db)

    ea = db.query(ExpertAdvisor).filter(ExpertAdvisor.id == license_row.ea_id).first()
    if not ea:
        raise HTTPException(status_code=404, detail="EA linked to license not found")

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