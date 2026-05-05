from app.database import engine
from sqlalchemy import text

def column_exists(conn, table_name, column_name):
    result = conn.execute(text(f"""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = :table AND column_name = :column
    """), {"table": table_name, "column": column_name})

    return result.fetchone() is not None


with engine.connect() as conn:

    # 🔥 license_balance
    if not column_exists(conn, "admins", "license_balance"):
        print("Adding license_balance...")
        conn.execute(text("ALTER TABLE admins ADD COLUMN license_balance INTEGER DEFAULT 0"))

    else:
        print("license_balance already exists")

    # 🔥 optional fields (future use)
    if not column_exists(conn, "admins", "license_used"):
        print("Adding license_used...")
        conn.execute(text("ALTER TABLE admins ADD COLUMN license_used INTEGER DEFAULT 0"))

    else:
        print("license_used already exists")

    if not column_exists(conn, "admins", "license_quota"):
        print("Adding license_quota...")
        conn.execute(text("ALTER TABLE admins ADD COLUMN license_quota INTEGER DEFAULT 0"))

    else:
        print("license_quota already exists")

    conn.commit()

print("✅ Database update complete")