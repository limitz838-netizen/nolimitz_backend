from dotenv import load_dotenv
load_dotenv(dotenv_path=".env.local", override=True)

from sqlalchemy import create_engine, text
import os

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)

statements = [
    """
    CREATE TABLE IF NOT EXISTS ticket_maps (
        id SERIAL PRIMARY KEY,
        license_id INTEGER NOT NULL,
        execution_id INTEGER NULL,
        master_ticket VARCHAR NOT NULL,
        client_ticket VARCHAR NULL,
        symbol VARCHAR NOT NULL,
        is_closed BOOLEAN NOT NULL DEFAULT FALSE,
        closed_by_client BOOLEAN NOT NULL DEFAULT FALSE,
        closed_at TIMESTAMP NULL,
        last_error VARCHAR NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    "ALTER TABLE ticket_maps ADD COLUMN IF NOT EXISTS execution_id INTEGER NULL",
    "ALTER TABLE ticket_maps ADD COLUMN IF NOT EXISTS master_ticket VARCHAR",
    "ALTER TABLE ticket_maps ADD COLUMN IF NOT EXISTS client_ticket VARCHAR",
    "ALTER TABLE ticket_maps ADD COLUMN IF NOT EXISTS symbol VARCHAR",
    "ALTER TABLE ticket_maps ADD COLUMN IF NOT EXISTS is_closed BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE ticket_maps ADD COLUMN IF NOT EXISTS closed_by_client BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE ticket_maps ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP NULL",
    "ALTER TABLE ticket_maps ADD COLUMN IF NOT EXISTS last_error VARCHAR NULL",
    "ALTER TABLE ticket_maps ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW()",
    "CREATE INDEX IF NOT EXISTS ix_ticket_maps_license_id ON ticket_maps (license_id)",
    "CREATE INDEX IF NOT EXISTS ix_ticket_maps_execution_id ON ticket_maps (execution_id)",
    "CREATE INDEX IF NOT EXISTS ix_ticket_maps_master_ticket ON ticket_maps (master_ticket)",
    "CREATE INDEX IF NOT EXISTS ix_ticket_maps_client_ticket ON ticket_maps (client_ticket)",
    "CREATE INDEX IF NOT EXISTS ix_ticket_maps_symbol ON ticket_maps (symbol)",
]

with engine.begin() as conn:
    for stmt in statements:
        conn.execute(text(stmt))

print("ticket_maps migration completed successfully.")