from typing import Optional

from sqlalchemy.orm import Session

from app.models import ClientMT5Account, ClientSymbolSetting, License


def normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def normalize_action(action: str) -> str:
    return (action or "").strip().lower()


def get_symbol_aliases(symbol: str) -> list[str]:
    base = normalize_symbol(symbol)

    alias_map = {
        "XAUUSD": ["XAUUSD", "XAUUSDM", "GOLD", "GOLDM", "XAUUSD.", "XAUUSDm"],
        "BTCUSD": ["BTCUSD", "BTCUSDM", "BTCUSDT", "BTCUSD.", "BTCUSDm"],
        "ETHUSD": ["ETHUSD", "ETHUSDM", "ETHUSDT", "ETHUSD.", "ETHUSDm"],
        "EURUSD": ["EURUSD", "EURUSDM", "EURUSD.", "EURUSDm"],
        "GBPUSD": ["GBPUSD", "GBPUSDM", "GBPUSD.", "GBPUSDm"],
        "USDJPY": ["USDJPY", "USDJPYM", "USDJPY.", "USDJPYm"],
    }

    return [normalize_symbol(x) for x in alias_map.get(base, [base])]


def license_can_receive_execution(license_row: License) -> bool:
    if not license_row:
        return False

    if not license_row.is_active:
        return False

    if not getattr(license_row, "execution_enabled", False):
        return False

    if getattr(license_row, "execution_started_at", None) is None:
        return False

    return True


def get_active_verified_mt5_account(db: Session, license_id: int) -> Optional[ClientMT5Account]:
    return db.query(ClientMT5Account).filter(
        ClientMT5Account.license_id == license_id,
        ClientMT5Account.is_active == True,
        ClientMT5Account.is_verified == True,
    ).first()


def find_matching_symbol_setting(
    db: Session,
    license_id: int,
    symbol: str,
) -> Optional[ClientSymbolSetting]:
    aliases = get_symbol_aliases(symbol)

    return db.query(ClientSymbolSetting).filter(
        ClientSymbolSetting.license_id == license_id,
        ClientSymbolSetting.symbol_name.in_(aliases),
        ClientSymbolSetting.enabled == True,
    ).first()


def direction_allows_trade(symbol_setting: ClientSymbolSetting, action: str) -> bool:
    if not symbol_setting:
        return False

    direction = normalize_action(symbol_setting.trade_direction)
    action = normalize_action(action)

    if direction not in ["buy", "sell", "both"]:
        return False

    if direction == "both":
        return True

    return direction == action


def get_execution_skip_reason(
    db: Session,
    license_row: License,
    event_symbol: str,
    event_type: str,
    event_action: Optional[str],
) -> tuple[Optional[str], Optional[ClientMT5Account], Optional[ClientSymbolSetting]]:
    if not license_row.is_active:
        return "license_inactive", None, None

    if not getattr(license_row, "execution_enabled", False):
        return "execution_disabled", None, None

    if getattr(license_row, "execution_started_at", None) is None:
        return "execution_not_started", None, None

    mt5 = get_active_verified_mt5_account(db, license_row.id)
    if not mt5:
        return "no_active_verified_mt5", None, None

    symbol_setting = find_matching_symbol_setting(db, license_row.id, event_symbol)
    if not symbol_setting:
        return "symbol_not_enabled", mt5, None

    if normalize_action(event_type) == "open":
        if not direction_allows_trade(symbol_setting, event_action or ""):
            return "direction_blocked", mt5, symbol_setting

    return None, mt5, symbol_setting