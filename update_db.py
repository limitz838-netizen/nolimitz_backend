from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://nolimitzuser:8CdrbHNlWrXw7OVN2NrZfg3yaRDqLZYC@dpg-d7fm0mdckfvc73fhpklg-a.oregon-postgres.render.com/nolimitzdb"

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS license_quota_requests (
            id SERIAL PRIMARY KEY,
            admin_id INTEGER NOT NULL,
            requested_amount INTEGER NOT NULL,
            status VARCHAR DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW(),
            processed_at TIMESTAMP
        );
    """))
    conn.commit()

print("DONE ✅")