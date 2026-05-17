from app.database import SessionLocal
from app.models import ClientMT5Account

db = SessionLocal()

accounts = db.query(ClientMT5Account).all()

deleted = 0

for acc in accounts:

    login = str(acc.login).strip() if acc.login else ""
    password = str(acc.password).strip() if acc.password else ""
    server = str(acc.server).strip() if acc.server else ""

    if (
        login == ""
        or login.lower() == "none"
        or password == ""
        or password.lower() == "none"
        or server == ""
        or server.lower() == "none"
    ):

        print(f"Deleting broken account ID {acc.id}")

        db.delete(acc)

        deleted += 1

db.commit()

print(f"Deleted {deleted} broken accounts")