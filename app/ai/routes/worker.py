"""
app/ai/routes/worker.py

The old GET /api/worker/active-accounts route used to live here. It had NO
authentication and returned every active MT5 login, password and server, so
it has been removed (17 Oct 2026). Nothing in the platform calls it: the
copier lanes, verifier and AI workers read the database directly.

The empty router stays so app/main.py keeps importing it unchanged.
"""
from fastapi import APIRouter

router = APIRouter(
    prefix="/api/worker",
    tags=["Worker"]
)
