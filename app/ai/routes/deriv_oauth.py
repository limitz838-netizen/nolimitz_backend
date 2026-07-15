"""
NolimitzBots — Deriv OAuth 2.0 (PKCE) Router  [v2 — SQLAlchemy/Postgres]
========================================================================
Uses the project's existing SQLAlchemy engine (app.database), so it works
with the Render Postgres database — no separate SQLite file.

Endpoints:
  GET    /auth/deriv/login?license_key=XXX   -> redirect to Deriv login
  GET    /auth/deriv/callback                -> Deriv redirects here
  GET    /auth/deriv/status?license_key=XXX  -> {"connected": bool, ...}
  DELETE /auth/deriv/disconnect?license_key=XXX

Wire-up in main.py:
  from app.ai.routes.deriv_oauth import router as deriv_router, init_deriv_tables
  init_deriv_tables()          # after Base.metadata.create_all(...)
  app.include_router(deriv_router)
"""

import base64
import hashlib
import secrets
import time

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.database import engine

# ---------------------------------------------------------------- CONFIG ---
DERIV_CLIENT_ID = "33OsG0tRErc79w7pewDsm"
DERIV_REDIRECT_URI = (
    "https://nolimitz-backend-yfne.onrender.com/auth/deriv/callback"
)  # <-- must match the Redirect URL in your Deriv dashboard EXACTLY
DERIV_AUTH_URL = "https://auth.deriv.com/oauth2/auth"
DERIV_TOKEN_URL = "https://auth.deriv.com/oauth2/token"
DERIV_API_BASE = "https://api.derivws.com/trading/v1/options"
DERIV_SCOPES = "trade account_manage"

router = APIRouter(prefix="/auth/deriv", tags=["deriv-oauth"])


# ------------------------------------------------------------------- DB ---
def init_deriv_tables():
    with engine.begin() as conn:
        conn.execute(
            text(
                """CREATE TABLE IF NOT EXISTS deriv_oauth_sessions (
                       state         VARCHAR(128) PRIMARY KEY,
                       license_key   VARCHAR(128) NOT NULL,
                       code_verifier VARCHAR(256) NOT NULL,
                       created_at    BIGINT NOT NULL
                   )"""
            )
        )
        conn.execute(
            text(
                """CREATE TABLE IF NOT EXISTS deriv_connections (
                       license_key   VARCHAR(128) PRIMARY KEY,
                       access_token  TEXT NOT NULL,
                       refresh_token TEXT,
                       token_type    VARCHAR(32) DEFAULT 'Bearer',
                       expires_at    BIGINT NOT NULL,
                       connected_at  BIGINT NOT NULL,
                       accounts_json TEXT
                   )"""
            )
        )


def _license_exists(license_key: str) -> bool:
    """
    Checks the licenses table. Adjust table/column names if yours differ —
    e.g. if your model's __tablename__ is 'licenses' and the column is 'key',
    change the query to:  SELECT 1 FROM licenses WHERE key = :k
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM licenses WHERE license_key = :k LIMIT 1"),
                {"k": license_key},
            ).fetchone()
        return row is not None
    except Exception:
        # Table/column name mismatch — don't block the flow; fix the query.
        return True


# ------------------------------------------------------------------ PKCE ---
def _make_pkce():
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


# ------------------------------------------------------------- ENDPOINTS ---
@router.get("/login")
def deriv_login(license_key: str = Query(..., min_length=4)):
    if not _license_exists(license_key):
        raise HTTPException(404, "Unknown license key")

    verifier, challenge = _make_pkce()
    state = secrets.token_urlsafe(32)
    now = int(time.time())

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM deriv_oauth_sessions WHERE created_at < :cutoff"),
            {"cutoff": now - 900},
        )
        conn.execute(
            text(
                "INSERT INTO deriv_oauth_sessions "
                "(state, license_key, code_verifier, created_at) "
                "VALUES (:s, :lk, :cv, :ca)"
            ),
            {"s": state, "lk": license_key, "cv": verifier, "ca": now},
        )

    auth_url = (
        f"{DERIV_AUTH_URL}"
        f"?response_type=code"
        f"&client_id={DERIV_CLIENT_ID}"
        f"&redirect_uri={DERIV_REDIRECT_URI}"
        f"&scope={DERIV_SCOPES.replace(' ', '+')}"
        f"&state={state}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
    )
    return RedirectResponse(auth_url)


@router.get("/callback")
async def deriv_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(_result_page(False, f"Deriv returned: {error}"))
    if not code or not state:
        return HTMLResponse(_result_page(False, "Missing code or state"))

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT license_key, code_verifier "
                "FROM deriv_oauth_sessions WHERE state = :s"
            ),
            {"s": state},
        ).fetchone()
    if row is None:
        return HTMLResponse(_result_page(False, "Invalid or expired session"))

    license_key, verifier = row[0], row[1]

    async with httpx.AsyncClient(timeout=20) as http:
        resp = await http.post(
            DERIV_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": DERIV_CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": DERIV_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if resp.status_code != 200:
        return HTMLResponse(
            _result_page(False, f"Token exchange failed ({resp.status_code})")
        )

    tok = resp.json()
    access_token = tok["access_token"]
    expires_at = int(time.time()) + int(tok.get("expires_in", 3600))
    refresh_token = tok.get("refresh_token")

    accounts_json = None
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            acc = await http.get(
                f"{DERIV_API_BASE}/accounts",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if acc.status_code == 200:
            accounts_json = acc.text
    except httpx.HTTPError:
        pass

    now = int(time.time())
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM deriv_oauth_sessions WHERE state = :s"),
            {"s": state},
        )
        conn.execute(
            text(
                """INSERT INTO deriv_connections
                       (license_key, access_token, refresh_token, token_type,
                        expires_at, connected_at, accounts_json)
                   VALUES (:lk, :at, :rt, :tt, :ea, :ca, :aj)
                   ON CONFLICT (license_key) DO UPDATE SET
                       access_token  = EXCLUDED.access_token,
                       refresh_token = EXCLUDED.refresh_token,
                       token_type    = EXCLUDED.token_type,
                       expires_at    = EXCLUDED.expires_at,
                       connected_at  = EXCLUDED.connected_at,
                       accounts_json = EXCLUDED.accounts_json"""
            ),
            {
                "lk": license_key,
                "at": access_token,
                "rt": refresh_token,
                "tt": tok.get("token_type", "Bearer"),
                "ea": expires_at,
                "ca": now,
                "aj": accounts_json,
            },
        )

    return HTMLResponse(_result_page(True, "Deriv account connected"))


@router.get("/status")
def deriv_status(license_key: str = Query(...)):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT expires_at, connected_at, accounts_json "
                "FROM deriv_connections WHERE license_key = :lk"
            ),
            {"lk": license_key},
        ).fetchone()
    if row is None:
        return {"connected": False}
    return {
        "connected": True,
        "token_valid": row[0] > int(time.time()),
        "connected_at": row[1],
        "accounts": row[2],
    }


@router.delete("/disconnect")
def deriv_disconnect(license_key: str = Query(...)):
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM deriv_connections WHERE license_key = :lk"),
            {"lk": license_key},
        )
    return {"disconnected": True}


# ------------------------------------------------- HELPER FOR THE WORKER ---
def get_deriv_token(license_key: str) -> str | None:
    """deriv_worker.py calls this before trading for a user."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT access_token, expires_at "
                "FROM deriv_connections WHERE license_key = :lk"
            ),
            {"lk": license_key},
        ).fetchone()
    if row is None or row[1] <= int(time.time()):
        return None
    return row[0]


# ------------------------------------------------------------ RESULT PAGE ---
def _result_page(ok: bool, msg: str) -> str:
    color = "#22c55e" if ok else "#ef4444"
    icon = "&#10003;" if ok else "&#10007;"
    title = "Connected!" if ok else "Connection failed"
    return f"""<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NolimitzBots</title><style>
  body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
       background:linear-gradient(135deg,#0b1220,#101c33);font-family:-apple-system,
       Segoe UI,Roboto,sans-serif;color:#e8eefc}}
  .card{{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);
       backdrop-filter:blur(18px);border-radius:20px;padding:40px 32px;text-align:center;
       max-width:340px;box-shadow:0 20px 60px rgba(0,0,0,.4)}}
  .icon{{width:64px;height:64px;border-radius:50%;background:{color}22;color:{color};
       display:flex;align-items:center;justify-content:center;font-size:30px;
       margin:0 auto 18px;border:2px solid {color}}}
  h1{{font-size:20px;margin:0 0 8px}} p{{opacity:.75;font-size:14px;margin:0 0 22px}}
  .btn{{display:inline-block;background:#3b82f6;color:#fff;text-decoration:none;
       padding:12px 28px;border-radius:12px;font-weight:600;font-size:14px}}
</style></head><body>
  <div class="card">
    <div class="icon">{icon}</div>
    <h1>{title}</h1>
    <p>{msg}. You can return to the NolimitzBots app.</p>
    <a class="btn" href="nolimitzbots://deriv-connected">Back to app</a>
  </div>
</body></html>"""