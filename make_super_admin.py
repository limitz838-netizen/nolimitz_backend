from passlib.context import CryptContext

from app.database import SessionLocal
from app.models import Admin

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)

db = SessionLocal()

admin = db.query(Admin).filter(
    Admin.email == "superadmin@nolimitz.com"
).first()

if admin:

    admin.password_hash = pwd_context.hash("admin12345")

    admin.plan = "PREMIUM"

    admin.is_super_admin = True

    db.commit()

    print("SUPER ADMIN PASSWORD RESET")

else:

    print("Admin not found")

db.close()