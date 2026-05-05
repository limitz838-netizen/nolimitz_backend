from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://nolimitzuser:8CdrbHNlWrXw7OVN2NrZfg3yaRDqLZYC@dpg-d7fm0mdckfvc73fhpklg-a.oregon-postgres.render.com/nolimitzdb"

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    conn.execute(text("""
        UPDATE admins
        SET 
            license_balance = COALESCE(license_balance, 0),
            license_used = COALESCE(license_used, 0),
            license_quota = COALESCE(license_quota, 0);
    """))

    conn.execute(text("""
        UPDATE admins
        SET 
            license_quota = 500,
            license_balance = 500,
            license_used = 0
        WHERE email = '123limitz@gmail.com';
    """))

    conn.commit()

print("✅ Database fixed successfully")