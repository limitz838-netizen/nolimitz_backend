"""
app/desk/access.py -- who the Desk trades for (Oct 2026, step 12)

The Desk IS the AI Auto Trader now. A user's account receives Desk trades when ALL of these hold:
  1. NOLIMITZ PRO licence  -- licence on a Pro EA (DESK_PRO_EA_IDS, default "1"), not a Telegram-only
                              subscription, active and not expired
  2. open to them          -- DESK_PUBLIC_ENABLED=true, or their licence id/key is in DESK_BETA_LICENSES
  3. MT5 account connected and verified
  4. AI Auto Trader ON     -- ClientMT5Account.ai_auto_trade, the same switch as the AI orb / Start AI
  5. gold enabled with a lot size (their own lot; their risk mode decides trades per signal)

There is no separate Desk on/off switch: turning the AI Auto Trader on or off is the switch.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

TRADES_PER_MODE = {"normal": 1, "medium": 3, "aggressive": 5}


def _env_set(name: str, default: str = "") -> set:
    return {x.strip() for x in os.getenv(name, default).split(",") if x.strip()}


def desk_public() -> bool:
    return os.getenv("DESK_PUBLIC_ENABLED", "false").strip().lower() == "true"


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def licence_live(lic, now=None) -> bool:
    now = now or datetime.now(timezone.utc)
    exp = _aware(lic.expires_at)
    return bool(lic.is_active and (exp is None or exp > now))


def is_pro(lic) -> bool:
    """A NOLIMITZ PRO licence. Telegram-Copier-only subscriptions are not Pro (an upgraded one is)."""
    if str(lic.ea_id) not in _env_set("DESK_PRO_EA_IDS", "1"):
        return False
    snap = lic.branding_snapshot if isinstance(lic.branding_snapshot, dict) else {}
    return snap.get("product") != "telegram_copier"


def open_to(lic, mt5_login=None) -> bool:
    """Pro, and the Desk is open to them (everyone once public, the beta list until then).
    DESK_BETA_LICENSES may hold licence ids, licence keys or MT5 logins."""
    if not is_pro(lic):
        return False
    beta = _env_set("DESK_BETA_LICENSES")
    return (desk_public() or str(lic.id) in beta or lic.license_key in beta
            or (mt5_login is not None and str(mt5_login).strip() in beta))
