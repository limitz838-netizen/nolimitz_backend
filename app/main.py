from fastapi import FastAPI

app = FastAPI(title="NolimitzBots Backend", version="1.0.0")


@app.get("/")
def root():
    return {"message": "NolimitzBots backend is live"}