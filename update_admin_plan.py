from sqlalchemy import text

from app.database import engine

with engine.connect() as conn:

    # =========================
    # ADD PLAN COLUMN
    # =========================

    try:

        conn.execute(
            text("""
                ALTER TABLE admins
                ADD COLUMN plan VARCHAR DEFAULT 'FREE'
            """)
        )

        print("plan column added")

    except Exception:

        print("plan column already exists")

    # =========================
    # ADD SUPER ADMIN COLUMN
    # =========================

    try:

        conn.execute(
            text("""
                ALTER TABLE admins
                ADD COLUMN is_super_admin BOOLEAN DEFAULT FALSE
            """)
        )

        print("is_super_admin column added")

    except Exception:

        print("is_super_admin column already exists")

    conn.commit()

print("Database updated successfully")