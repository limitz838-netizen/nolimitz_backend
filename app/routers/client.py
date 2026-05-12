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
from app.security import encrypt_text
from app.services.metaapi_service import MetaApiService

router = APIRouter(prefix="/client", tags=["Client"])


# =========================
# DEPENDENCIES
# =========================
def get_metaapi_service() -> MetaApiService:
    """Dependency for MetaApiService"""
    return MetaApiService()


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
        raise HTTPException(
            status_code=403,
            detail="License key already used on another device"
        )

    license_row.activated_device_name = device_name
    license_row.last_seen_at = now

    if not db.query(ClientActivation).filter(
        ClientActivation.license_id == license_row.id
    ).first():
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
        row.mt_login = payload.mt_login.strip()
        row.mt_password = encrypt_text(payload.mt_password)
        row.mt_server = payload.mt_server.strip()
        row.verification_error = None
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
        else:
            try:
                account = await metaapi_service.get_account(
                    row.metaapi_account_id
                )

            except Exception:

                # recreate MetaApi account if missing
                account = await metaapi_service.create_mt5_account(
                    login=payload.mt_login,
                    password=payload.mt_password,
                    server=payload.mt_server,
                    name=f"Nolimitz-{license_row.license_key[:12]}"
                )

                row.metaapi_account_id = account.id
                db.commit()

        await metaapi_service.deploy_account_and_wait(account)

        result = await metaapi_service.get_account_info(account.id)
        info = result["info"]

        # Update success data
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

        return build_mt5_response("MT5 connected successfully", license_row, row)

    except Exception as e:
        error_msg = str(e)[:500]
        row.is_verified = False
        row.is_active = False
        row.verification_error = error_msg
        row.last_verified_at = utc_now()
        db.commit()
        raise HTTPException(status_code=400, detail=error_msg)


# =========================
# REMAINING ENDPOINTS
# =========================
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


# Add the rest of your endpoints (symbols, robot, worker) here...
# You can copy them from the previous complete version.

@router.post("/robot/start", response_model=ClientRobotControlResponse)
def start_client_robot(
    payload: ClientRobotControlRequest,
    db: Session = Depends(get_db)
):
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
def stop_client_robot(
    payload: ClientRobotControlRequest,
    db: Session = Depends(get_db)
):
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