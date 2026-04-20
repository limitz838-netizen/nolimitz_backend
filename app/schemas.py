from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field


# =========================
# ADMIN
# =========================
class AdminSignupRequest(BaseModel):
    full_name: str
    email: EmailStr
    password: str


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


class AdminProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    phone: Optional[str] = None
    support_email: Optional[EmailStr] = None
    telegram: Optional[str] = None
    whatsapp: Optional[str] = None
    company_name: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    admin_id: int
    admin_code: int
    full_name: str
    email: EmailStr
    role: str
    is_approved: bool


class AdminMeResponse(BaseModel):
    admin_id: int
    admin_code: int
    full_name: str
    email: EmailStr
    role: str
    is_approved: bool
    is_active: bool
    display_name: Optional[str] = None
    logo_url: Optional[str] = None
    phone: Optional[str] = None
    support_email: Optional[str] = None
    telegram: Optional[str] = None
    whatsapp: Optional[str] = None
    company_name: Optional[str] = None


class AdminApprovalResponse(BaseModel):
    message: str
    admin_id: int
    is_approved: bool


class AdminListItem(BaseModel):
    admin_id: int
    full_name: str
    email: EmailStr
    role: str
    is_approved: bool
    is_active: bool


class LogoUploadResponse(BaseModel):
    message: str
    logo_url: str


class BasicMessageResponse(BaseModel):
    message: str


class DeviceLockResetResponse(BaseModel):
    message: str
    license_key: str
    activated_device_id: Optional[str] = None
    activated_device_name: Optional[str] = None


# =========================
# EA
# =========================
class EASymbolsRequest(BaseModel):
    symbols: List[str]


class EASymbolItem(BaseModel):
    id: int
    symbol_name: str
    enabled: bool

    class Config:
        from_attributes = True


class EACreateRequest(BaseModel):
    name: str
    code_name: str
    version: Optional[str] = None
    description: Optional[str] = None
    is_shareable: bool = False


class EAUpdateRequest(BaseModel):
    name: Optional[str] = None
    code_name: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    is_shareable: Optional[bool] = None
    is_active: Optional[bool] = None


class EAItem(BaseModel):
    id: int
    name: str
    code_name: str
    ea_code: str
    version: Optional[str] = None
    description: Optional[str] = None
    is_shareable: bool
    is_active: bool
    symbols: List[EASymbolItem] = Field(default_factory=list)

    class Config:
        from_attributes = True


class EALinkRequest(BaseModel):
    ea_code: str


class EALinkItem(BaseModel):
    id: int
    source_admin_id: int
    target_admin_id: int
    source_ea_id: int
    source_ea_code: str
    status: str
    is_active: bool

    class Config:
        from_attributes = True


# =========================
# LICENSE
# =========================
class LicenseCreateRequest(BaseModel):
    ea_id: int
    client_name: str
    client_email: EmailStr
    duration: str


class LicenseItem(BaseModel):
    id: int
    license_key: str
    client_name: str
    client_email: str
    expires_at: Optional[datetime] = None
    is_active: bool

    class Config:
        from_attributes = True


class LicenseResponse(BaseModel):
    message: str
    license: LicenseItem


# =========================
# CLIENT ACTIVATION
# =========================
class ClientActivateRequest(BaseModel):
    license_key: str
    device_id: str
    device_name: Optional[str] = None


class ClientActivateResponse(BaseModel):
    message: str
    license_key: str
    client_name: str
    client_email: str
    mode_type: str
    expires_at: datetime
    ea_name: str
    ea_code_name: str
    branding: Dict[str, Any]
    activated_device_id: Optional[str] = None
    activated_device_name: Optional[str] = None


class ClientLicenseRequest(BaseModel):
    license_key: str


# =========================
# CLIENT MT5
# =========================
class ClientMT5SaveRequest(BaseModel):
    license_key: str
    mt_login: str
    mt_password: str
    mt_server: str


class ClientMT5StatusRequest(BaseModel):
    license_key: str


class ClientMT5ReverifyRequest(BaseModel):
    license_key: str


class ClientMT5Response(BaseModel):
    message: str
    license_key: str
    mt_login: str
    mt_server: str
    is_active: bool
    verified: bool
    account_name: Optional[str] = None
    broker_name: Optional[str] = None
    balance: Optional[float] = None
    equity: Optional[float] = None
    last_verified_at: Optional[datetime] = None


class ClientMT5StatusResponse(BaseModel):
    license_key: str
    mt_login: Optional[str] = None
    mt_server: Optional[str] = None
    is_active: bool
    verified: bool
    account_name: Optional[str] = None
    broker_name: Optional[str] = None
    balance: Optional[float] = None
    equity: Optional[float] = None
    last_verified_at: Optional[datetime] = None
    status: str
    message: str


class MT5WorkerRegisterRequest(BaseModel):
    worker_name: str
    worker_type: str = "both"
    terminal_path: str
    data_path: Optional[str] = None


class MT5WorkerResponse(BaseModel):
    id: int
    worker_name: str
    worker_type: str
    terminal_path: str
    data_path: Optional[str] = None
    is_active: bool
    is_busy: bool
    current_license_id: Optional[int] = None
    last_heartbeat: Optional[datetime] = None
    last_error: Optional[str] = None

    class Config:
        from_attributes = True


# =========================
# CLIENT SYMBOL SETTINGS
# =========================
class ClientSymbolSettingSave(BaseModel):
    license_key: str
    symbol_name: str
    trade_direction: str
    lot_size: str
    max_open_trades: int
    trades_per_signal: int = 1
    enabled: bool = True


class ClientSymbolSettingOut(BaseModel):
    id: int
    symbol_name: str
    trade_direction: str
    lot_size: str
    max_open_trades: int
    trades_per_signal: int = 1
    enabled: bool

    class Config:
        from_attributes = True


class ClientRemoveSymbolRequest(BaseModel):
    license_key: str
    symbol_name: str


class ClientRemoveSymbolResponse(BaseModel):
    message: str
    symbol_name: str
    enabled: bool

class ClientTradeHistoryRequest(BaseModel):
    license_key: str
    limit: int = 30


class ClientTradeHistoryItem(BaseModel):
    id: int
    symbol: str
    action: Optional[str] = None
    event_type: str
    status: str
    lot_size: Optional[str] = None
    price: Optional[str] = None
    sl: Optional[str] = None
    tp: Optional[str] = None
    comment: Optional[str] = None
    error_message: Optional[str] = None
    client_ticket: Optional[str] = None
    master_ticket: str
    created_at: datetime

    class Config:
        from_attributes = True


# =========================
# SIGNALS
# =========================
class SignalPushRequest(BaseModel):
    ea_id: int
    symbol: str
    action: str
    sl: Optional[str] = None
    tp: Optional[str] = None
    comment: Optional[str] = None


class SignalItem(BaseModel):
    id: int
    ea_id: int
    symbol: str
    action: str
    sl: Optional[str] = None
    tp: Optional[str] = None
    comment: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class ClientSignalsRequest(BaseModel):
    license_key: str


# =========================
# ROBOT
# =========================
class RobotTradeCreateRequest(BaseModel):
    ea_id: int
    symbol: str
    action: str
    lot_size: Optional[str] = None
    sl: Optional[str] = None
    tp: Optional[str] = None
    comment: Optional[str] = None


class RobotTradeItem(BaseModel):
    id: int
    ea_id: int
    symbol: str
    action: str
    lot_size: Optional[str] = None
    sl: Optional[str] = None
    tp: Optional[str] = None
    comment: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# =========================
# COPIER
# =========================
class CopierOpenTradeRequest(BaseModel):
    ea_id: int
    master_ticket: str
    symbol: str
    action: str
    sl: Optional[str] = None
    tp: Optional[str] = None
    price: Optional[str] = None
    comment: Optional[str] = None


class CopierModifyTradeRequest(BaseModel):
    ea_id: int
    master_ticket: str
    symbol: str
    sl: Optional[str] = None
    tp: Optional[str] = None
    price: Optional[str] = None
    comment: Optional[str] = None


class CopierCloseTradeRequest(BaseModel):
    ea_id: int
    master_ticket: str
    symbol: str
    comment: Optional[str] = None


class CopierTradeEventItem(BaseModel):
    id: int
    ea_id: int
    ea_code: str
    event_type: str
    master_ticket: str
    symbol: str
    action: Optional[str] = None
    lot_size: Optional[str] = None
    sl: Optional[str] = None
    tp: Optional[str] = None
    price: Optional[str] = None
    comment: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class TradeExecutionItem(BaseModel):
    id: int
    copier_event_id: int
    license_id: int
    ea_id: int
    master_ticket: str
    client_ticket: Optional[str] = None
    symbol: str
    action: Optional[str] = None
    lot_size: Optional[str] = None
    sl: Optional[str] = None
    tp: Optional[str] = None
    price: Optional[str] = None
    comment: Optional[str] = None
    event_type: str
    status: str
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CreateExecutionsResponse(BaseModel):
    message: str
    event_id: int
    total_created: int
    executions: List[TradeExecutionItem]


class ExecutionUpdateRequest(BaseModel):
    status: str
    client_ticket: Optional[str] = None
    error_message: Optional[str] = None


class TradeTicketMapItem(BaseModel):
    id: int
    license_id: int
    ea_id: int
    master_ticket: str
    child_ticket_index: int
    client_ticket: str
    symbol: str
    action: Optional[str] = None
    is_open: bool
    manually_closed: bool
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# =========================
# MASTER ACCOUNT
# =========================
class MasterAccountSaveRequest(BaseModel):
    ea_id: int
    mt_login: str
    mt_password: str
    mt_server: str


class MasterAccountVerifyRequest(BaseModel):
    ea_id: int
    mt_login: str
    mt_password: str
    mt_server: str


class MasterAccountResponse(BaseModel):
    message: str
    ea_id: int
    mt_login: str
    mt_server: str
    is_connected: bool
    account_name: Optional[str] = None
    broker_name: Optional[str] = None


class ClientRobotControlRequest(BaseModel):
    license_key: str


class ClientRobotControlResponse(BaseModel):
    message: str
    license_key: str
    execution_enabled: bool
    execution_started_at: Optional[datetime] = None