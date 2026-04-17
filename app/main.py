import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.models import Admin, AdminProfile
from app.routers import mt5_workers
from app.routers.admin import router as admin_router
from app.routers.client import router as client_router
from app.routers.copier import router as copier_router
from app.routers.ea import router as ea_router
from app.routers.license import router as license_router
from app.routers.master_account import router as master_account_router
from app.routers.robot import router as robot_router
from app.routers.signals import router as signals_router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="NolimitzBots Backend", version="1.0.0")

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def seed_super_admin():
    email = os.getenv("SUPERADMIN_EMAIL", "superadmin@nolimitz.com")
    password = os.getenv("SUPERADMIN_PASSWORD", "admin12345")

    db: Session = SessionLocal()
    try:
        existing = db.query(Admin).filter(Admin.email == email).first()
        if existing:
            return

        super_admin = Admin(
            admin_code=100,
            full_name="Super Admin",
            email=email,
            password_hash=hash_password(password),
            role="super_admin",
            is_approved=True,
            is_active=True,
        )
        db.add(super_admin)
        db.commit()
        db.refresh(super_admin)

        profile = AdminProfile(
            admin_id=super_admin.id,
            display_name="NolimitzBots",
            company_name="NolimitzBots",
            support_email="support@nolimitz.com",
        )
        db.add(profile)
        db.commit()
    finally:
        db.close()


seed_super_admin()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://nolimitzbots.co.ke",
        "https://www.nolimitzbots.co.ke",
        "https://nolimitz-admin-orn6h91o2-limitz838-2833s-projects.vercel.app",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

app.include_router(admin_router)
app.include_router(ea_router)
app.include_router(license_router)
app.include_router(client_router)
app.include_router(signals_router)
app.include_router(robot_router)
app.include_router(copier_router)
app.include_router(mt5_workers.router)
app.include_router(master_account_router)


@app.get("/")
def root():
    return {"message": "NolimitzBots backend is running"}


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="NolimitzBots Backend",
        version="1.0.0",
        description="API for NolimitzBots",
        routes=app.routes,
    )

    openapi_schema.setdefault("components", {})
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }

    public_routes = {
        "/",
        "/admin/login",
        "/admin/signup",
        "/openapi.json",
    }

    for path, path_item in openapi_schema["paths"].items():
        if path in public_routes:
            continue

        for method in path_item:
            path_item[method]["security"] = [{"BearerAuth": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi