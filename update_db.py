from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://nolimitzuser:8CdrbHNlWrXw7OVN2NrZfg3yaRDqLZYC@dpg-d7fm0mdckfvc73fhpklg-a.oregon-postgres.render.com/nolimitzdb"

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    conn.execute(text("ALTER TABLE admins ADD COLUMN IF NOT EXISTS license_quota INTEGER DEFAULT 0;"))
    conn.execute(text("ALTER TABLE admins ADD COLUMN IF NOT EXISTS license_used INTEGER DEFAULT 0;"))
    conn.commit()

print("DONE ✅")