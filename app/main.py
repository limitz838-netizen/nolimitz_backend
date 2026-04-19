from fastapi import FastAPI

from app.routers import admin, client, signals

app = FastAPI(title="NolimitzBots Backend", version="1.0.0")


@app.get("/")
def root():
    return {"message": "NolimitzBots backend is live"}


# CONNECT ROUTERS
app.include_router(admin.router)
app.include_router(client.router)
app.include_router(signals.router)