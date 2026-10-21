import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.ai.models.ai_user import AIUser
from app.models import Admin


router = APIRouter(
    prefix="/api/ai",
    tags=["AI Auth"]
)

# The signing key used to be hard-coded here ("nolimitz_ai_secret"), so anyone
# who saw this file could forge a token. It now comes from the environment:
# AI_JWT_SECRET if set, otherwise the (already rotated) JWT_SECRET.
# Any other file that signs or checks these tokens must import SECRET_KEY
# from here instead of keeping its own copy.
SECRET_KEY = os.getenv("AI_JWT_SECRET") or os.getenv("JWT_SECRET")
if not SECRET_KEY:
    raise RuntimeError("Set AI_JWT_SECRET (or JWT_SECRET) in the environment")
ALGORITHM = "HS256"

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)

# A real bcrypt hash of a random string: checked when the email is unknown so
# a wrong email takes as long as a wrong password (no email probing by timing).
try:
    _DUMMY_HASH = pwd_context.hash(os.urandom(16).hex())
except Exception:          # never let this stop the app from starting
    _DUMMY_HASH = None


class SignupSchema(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginSchema(BaseModel):
    email: EmailStr
    password: str


# =========================
# AI USER SIGNUP
# =========================

@router.post("/signup")
def signup(data: SignupSchema):

    db: Session = SessionLocal()
    try:
        existing_user = (
            db.query(AIUser)
            .filter(AIUser.email == data.email)
            .first()
        )

        if existing_user:
            raise HTTPException(
                status_code=400,
                detail="Email already registered"
            )

        hashed_password = pwd_context.hash(
            data.password
        )

        new_user = AIUser(
            name=data.name,
            email=data.email,
            password_hash=hashed_password
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return {
            "message": "Account created successfully",
            "credits": new_user.credits
        }
    finally:
        # Always hand the connection back, even when we raise. Before this,
        # every refused request leaked one pooled connection; ~15 of them
        # took the whole API down on 21 Oct (QueuePool timeout).
        db.close()


# =========================
# ADMIN LOGIN
# =========================

@router.post("/login")
def login(data: LoginSchema):

    db: Session = SessionLocal()
    try:
        admin = (
            db.query(Admin)
            .filter(Admin.email == data.email)
            .first()
        )

        if not admin:
            if _DUMMY_HASH:
                try:
                    pwd_context.verify(data.password, _DUMMY_HASH)
                except Exception:
                    pass
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials"
            )

        valid_password = pwd_context.verify(
            data.password,
            admin.password_hash
        )

        # Switched-off admins (is_active = false) and admins nobody approved
        # must not get a token here either -- the main admin login already
        # refuses them, this one did not.
        allowed = bool(admin.is_active) and (
            admin.role == "super_admin" or bool(admin.is_approved)
        )

        if not valid_password or not allowed:
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials"
            )

        token_data = {
            "admin_id": admin.id,
            "exp": datetime.utcnow() + timedelta(days=7)
        }

        token = jwt.encode(
            token_data,
            SECRET_KEY,
            algorithm=ALGORITHM
        )

        return {
            "access_token": token,
            "admin": {
                "id": admin.id,
                "email": admin.email,
                "role": admin.role,
                "plan": admin.plan,
                "is_super_admin": admin.is_super_admin
            }
        }
    finally:
        db.close()
