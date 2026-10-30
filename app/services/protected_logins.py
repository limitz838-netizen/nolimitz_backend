"""
app/services/protected_logins.py -- MT5 logins that must never be connected as a CUSTOMER account (30 Oct 2026)

Why: if the master login is also a customer account, the copier copies each master trade INTO the master, the
bridge sees those copies as new master trades and broadcasts them, and every customer gets every trade several
times (incidents 20-26 Oct and 29 Oct 2026). Desk demo accounts must stay clean for the same reason.

Protected = every login in master_accounts + PROTECTED_MT5_LOGINS (comma list, default: the Desk demo accounts).
"""
from __future__ import annotations

import os

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

DEFAULT = "81866154,24576071"
MESSAGE = ("This MT5 account is a NolimitzBots system account (master or AI Desk) and can't be connected as a "
           "customer account. Please connect your own MT5 account.")


def _norm(login) -> str:
    return str(login or "").strip().split(".")[0]


def protected_logins(db: Session) -> set:
    out = {_norm(x) for x in os.getenv("PROTECTED_MT5_LOGINS", DEFAULT).split(",") if _norm(x)}
    try:
        out |= {_norm(r[0]) for r in db.execute(text("select mt_login from master_accounts")).fetchall() if _norm(r[0])}
    except Exception:
        pass                                   # never block a normal customer because of a lookup error
    return out


def refuse_if_protected(db: Session, login) -> None:
    if _norm(login) and _norm(login) in protected_logins(db):
        raise HTTPException(status_code=400, detail=MESSAGE)
