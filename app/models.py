from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True)
    admin_code = Column(Integer, unique=True, index=True, nullable=False)

    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)

    role = Column(String, nullable=False, default="admin")
    is_approved = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    profile = relationship("AdminProfile", back_populates="admin", uselist=False, cascade="all, delete-orphan")
    eas = relationship("ExpertAdvisor", back_populates="admin", cascade="all, delete-orphan")
    licenses = relationship("License", back_populates="admin")
    signals = relationship("Signal", back_populates="admin")
    robot_trades = relationship("RobotTrade", back_populates="admin")
    source_copier_events = relationship("CopierTradeEvent", back_populates="source_admin")

class MasterAccount(Base):
    __tablename__ = "master_accounts"

    id = Column(Integer, primary_key=True, index=True)

    admin_id = Column(Integer, ForeignKey("admins.id"), nullable=False)
    ea_id = Column(Integer, ForeignKey("expert_advisors.id"), nullable=False, index=True)

    mt_login = Column(String, nullable=False)
    mt_password = Column(String, nullable=False)
    mt_server = Column(String, nullable=False)

    metaapi_account_id = Column(String, nullable=True, unique=True, index=True)
    metaapi_state = Column(String, nullable=True)
    metaapi_connection_status = Column(String, nullable=True)
    metaapi_region = Column(String, nullable=True)
    metaapi_type = Column(String, nullable=True)
    metaapi_reliability = Column(String, nullable=True)

    is_connected = Column(Boolean, default=False)
    account_name = Column(String, nullable=True)
    broker_name = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    admin = relationship("Admin")
    ea = relationship("ExpertAdvisor")

class AdminProfile(Base):
    __tablename__ = "admin_profiles"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey("admins.id"), unique=True, nullable=False, index=True)

    display_name = Column(String, nullable=True)
    logo_url = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    support_email = Column(String, nullable=True)
    telegram = Column(String, nullable=True)
    whatsapp = Column(String, nullable=True)
    company_name = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    admin = relationship("Admin", back_populates="profile")


class ExpertAdvisor(Base):
    __tablename__ = "expert_advisors"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)

    name = Column(String, nullable=False)
    code_name = Column(String, nullable=False, index=True)
    ea_code = Column(String, unique=True, index=True, nullable=False)
    version = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    mode_type = Column(String, nullable=False, default="both")
    is_shareable = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    admin = relationship("Admin", back_populates="eas")
    symbols = relationship("EASymbol", back_populates="ea", cascade="all, delete-orphan")
    licenses = relationship("License", back_populates="ea")
    signals = relationship("Signal", back_populates="ea")
    robot_trades = relationship("RobotTrade", back_populates="ea")
    copier_events = relationship("CopierTradeEvent", back_populates="ea")
    trade_executions = relationship("TradeExecution", back_populates="ea")


class AdminEALink(Base):
    __tablename__ = "admin_ea_links"

    id = Column(Integer, primary_key=True, index=True)

    source_admin_id = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)
    target_admin_id = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)
    source_ea_id = Column(Integer, ForeignKey("expert_advisors.id"), nullable=False, index=True)
    source_ea_code = Column(String, nullable=False, index=True)

    status = Column(String, nullable=False, default="approved")
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class EASymbol(Base):
    __tablename__ = "ea_symbols"

    id = Column(Integer, primary_key=True, index=True)
    ea_id = Column(Integer, ForeignKey("expert_advisors.id"), nullable=False, index=True)

    symbol_name = Column(String, nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    ea = relationship("ExpertAdvisor", back_populates="symbols")


class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)

    admin_id = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)
    ea_id = Column(Integer, ForeignKey("expert_advisors.id"), nullable=False, index=True)

    license_key = Column(String, unique=True, index=True, nullable=False)

    client_name = Column(String, nullable=False)
    client_email = Column(String, nullable=False, index=True)

    mode_type = Column(String, nullable=False)

    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)

    activated_device_id = Column(String, nullable=True, index=True)
    activated_device_name = Column(String, nullable=True)
    first_activated_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    branding_snapshot = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    execution_enabled = Column(Boolean, nullable=False, default=False)
    execution_started_at = Column(DateTime(timezone=True), nullable=True)

    admin = relationship("Admin", back_populates="licenses")
    ea = relationship("ExpertAdvisor", back_populates="licenses")
    activation = relationship("ClientActivation", back_populates="license", uselist=False, cascade="all, delete-orphan")
    mt5_account = relationship("ClientMT5Account", back_populates="license", uselist=False, cascade="all, delete-orphan")
    verification_jobs = relationship("MT5VerificationJob", back_populates="license", cascade="all, delete-orphan")
    symbol_settings = relationship("ClientSymbolSetting", back_populates="license", cascade="all, delete-orphan")
    executions = relationship("TradeExecution", back_populates="license")


class ClientActivation(Base):
    __tablename__ = "client_activations"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), unique=True, nullable=False, index=True)

    activated = Column(Boolean, nullable=False, default=True)
    activated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    license = relationship("License", back_populates="activation")


class ClientMT5Account(Base):
    __tablename__ = "client_mt5_accounts"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), unique=True, nullable=False, index=True)

    # Original MT5 credentials entered by client
    mt_login = Column(String, nullable=False, index=True)
    mt_password = Column(String, nullable=False)
    mt_server = Column(String, nullable=False, index=True)

    # MetaApi cloud account details
    metaapi_account_id = Column(String, nullable=True, unique=True, index=True)
    metaapi_state = Column(String, nullable=True)  # CREATED / DEPLOYING / DEPLOYED / etc
    metaapi_connection_status = Column(String, nullable=True)  # CONNECTED / DISCONNECTED / DISCONNECTED_FROM_BROKER
    metaapi_region = Column(String, nullable=True)
    metaapi_type = Column(String, nullable=True)  # usually cloud-g2
    metaapi_reliability = Column(String, nullable=True)  # regular / high

    # Account info fetched after successful sync
    account_name = Column(String, nullable=True)
    broker_name = Column(String, nullable=True)
    balance = Column(Float, nullable=True, default=0)
    equity = Column(Float, nullable=True, default=0)

    # Verification / sync info
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    verification_error = Column(String, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    is_verified = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    license = relationship("License", back_populates="mt5_account")
    verification_jobs = relationship("MT5VerificationJob", back_populates="mt5_account", cascade="all, delete-orphan")


class MT5Worker(Base):
    __tablename__ = "mt5_workers"

    id = Column(Integer, primary_key=True, index=True)

    worker_name = Column(String, unique=True, nullable=False, index=True)
    worker_type = Column(String, nullable=False, default="both")

    terminal_path = Column(String, nullable=False)
    data_path = Column(String, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    is_busy = Column(Boolean, nullable=False, default=False)

    current_license_id = Column(Integer, ForeignKey("licenses.id"), nullable=True, index=True)

    last_heartbeat = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    jobs = relationship("MT5VerificationJob", back_populates="worker")


class MT5VerificationJob(Base):
    __tablename__ = "mt5_verification_jobs"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), nullable=False)
    client_mt5_account_id = Column(Integer, ForeignKey("client_mt5_accounts.id"), nullable=False)

    worker_id = Column(Integer, ForeignKey("mt5_workers.id"), nullable=True)
    worker_name = Column(String, nullable=True)

    status = Column(String, default="pending")
    error_message = Column(Text, nullable=True)

    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=5)

    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    license = relationship("License", back_populates="verification_jobs")
    mt5_account = relationship("ClientMT5Account", back_populates="verification_jobs")
    worker = relationship("MT5Worker", back_populates="jobs")


class ClientSymbolSetting(Base):
    __tablename__ = "client_symbol_settings"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), nullable=False, index=True)

    symbol_name = Column(String, nullable=False, index=True)
    trade_direction = Column(String, nullable=False, default="both")
    trades_per_signal = Column(Integer, nullable=False, default=1)
    lot_size = Column(String, nullable=False, default="0.01")
    max_open_trades = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    license = relationship("License", back_populates="symbol_settings")


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, index=True)

    admin_id = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)
    ea_id = Column(Integer, ForeignKey("expert_advisors.id"), nullable=False, index=True)

    symbol = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)
    sl = Column(String, nullable=True)
    tp = Column(String, nullable=True)
    comment = Column(String, nullable=True)

    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    admin = relationship("Admin", back_populates="signals")
    ea = relationship("ExpertAdvisor", back_populates="signals")


class RobotTrade(Base):
    __tablename__ = "robot_trades"

    id = Column(Integer, primary_key=True, index=True)

    admin_id = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)
    ea_id = Column(Integer, ForeignKey("expert_advisors.id"), nullable=False, index=True)

    symbol = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)
    lot_size = Column(String, nullable=True)
    sl = Column(String, nullable=True)
    tp = Column(String, nullable=True)
    comment = Column(String, nullable=True)

    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    admin = relationship("Admin", back_populates="robot_trades")
    ea = relationship("ExpertAdvisor", back_populates="robot_trades")


class CopierTradeEvent(Base):
    __tablename__ = "copier_trade_events"

    id = Column(Integer, primary_key=True, index=True)

    source_admin_id = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)
    ea_id = Column(Integer, ForeignKey("expert_advisors.id"), nullable=False, index=True)
    ea_code = Column(String, nullable=False, index=True)

    event_type = Column(String, nullable=False)
    master_ticket = Column(String, nullable=False, index=True)

    symbol = Column(String, nullable=False, index=True)
    action = Column(String, nullable=True)
    lot_size = Column(String, nullable=True)
    sl = Column(String, nullable=True)
    tp = Column(String, nullable=True)
    price = Column(String, nullable=True)
    comment = Column(String, nullable=True)

    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    source_admin = relationship("Admin", back_populates="source_copier_events")
    ea = relationship("ExpertAdvisor", back_populates="copier_events")
    executions = relationship("TradeExecution", back_populates="copier_event", cascade="all, delete-orphan")


class TradeExecution(Base):
    __tablename__ = "trade_executions"

    id = Column(Integer, primary_key=True, index=True)

    copier_event_id = Column(Integer, ForeignKey("copier_trade_events.id"), nullable=False, index=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), nullable=False, index=True)
    ea_id = Column(Integer, ForeignKey("expert_advisors.id"), nullable=False, index=True)

    master_ticket = Column(String, nullable=False, index=True)
    client_ticket = Column(String, nullable=True, index=True)

    symbol = Column(String, nullable=False, index=True)
    action = Column(String, nullable=True)
    lot_size = Column(String, nullable=True)
    sl = Column(String, nullable=True)
    tp = Column(String, nullable=True)
    price = Column(String, nullable=True)
    comment = Column(String, nullable=True)

    event_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    error_message = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    copier_event = relationship("CopierTradeEvent", back_populates="executions")
    license = relationship("License", back_populates="executions")
    ea = relationship("ExpertAdvisor", back_populates="trade_executions")


class TradeTicketMap(Base):
    __tablename__ = "ticket_maps"

    id = Column(Integer, primary_key=True, index=True)

    license_id = Column(Integer, index=True, nullable=False)
    execution_id = Column(Integer, index=True, nullable=True)

    master_ticket = Column(String, index=True, nullable=False)
    client_ticket = Column(String, index=True, nullable=True)

    symbol = Column(String, index=True, nullable=False)

    is_closed = Column(Boolean, default=False, nullable=False)
    closed_by_client = Column(Boolean, default=False, nullable=False)

    closed_at = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)