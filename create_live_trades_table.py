from app.database import Base, engine
from app.models import LiveTrade

print("CREATING LIVE TRADES TABLE...")

Base.metadata.create_all(bind=engine)

print("DONE")