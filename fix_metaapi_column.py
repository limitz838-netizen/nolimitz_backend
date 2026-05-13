from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://nolimitzuser:8CdrbHNlWrXw7OVN2NrZfg3yaRDqLZYC@dpg-d7fm0mdckfvc73fhpklg-a.oregon-postgres.render.com/nolimitzdb"

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:

    try:
        conn.execute(text("""
            ALTER TABLE client_mt5_accounts
            ADD COLUMN metaapi_account_id VARCHAR;
        """))

        conn.commit()

        print("SUCCESS")

    except Exception as e:
        print("ERROR:", e)