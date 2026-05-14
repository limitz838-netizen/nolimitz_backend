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

SECRET_KEY = "nolimitz_ai_secret"
ALGORITHM = "HS256"

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


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

    db.close()

    return {
        "message": "Account created successfully",
        "credits": new_user.credits
    }


# =========================
# ADMIN LOGIN
# =========================

@router.post("/login")
def login(data: LoginSchema):

    db: Session = SessionLocal()

    admin = (
        db.query(Admin)
        .filter(Admin.email == data.email)
        .first()
    )

    if not admin:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials"
        )

    valid_password = pwd_context.verify(
        data.password,
        admin.password_hash
    )

    if not valid_password:
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

    db.close()

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