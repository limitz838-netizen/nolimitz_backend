"""
NolimitzBots — Deriv OAuth 2.0 (PKCE) Router  [v5 — silent token refresh]
=========================================================================
Deriv connection is available to ALL signed-in users (free + licensed).
Connections are keyed on the user's account ID from NolimitzBots signup.

Endpoints:
  GET    /auth/deriv/login?user_id=XXX    -> redirect to Deriv login
  GET    /auth/deriv/callback             -> Deriv redirects here
  GET    /auth/deriv/status?user_id=XXX   -> {"connected": bool, ...}
  DELETE /auth/deriv/disconnect?user_id=XXX

v5 changes:
  - get_deriv_token() now silently refreshes expired access tokens using
    the stored refresh_token, so users stay connected beyond 1 hour.
"""

import base64
import hashlib
import secrets
import time

import httpx
from fastapi import APIRouter, Query
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

WEBSITE_URL = "https://nolimitzbots.co.ke/"  # where users return after connecting

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
def deriv_login(user_id: str = Query(..., min_length=1)):
    """Open to all signed-in users — free scanner users included."""
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
                "VALUES (:s, :uid, :cv, :ca)"
            ),
            {"s": state, "uid": user_id, "cv": verifier, "ca": now},
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

    user_id, verifier = row[0], row[1]

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
                   VALUES (:uid, :at, :rt, :tt, :ea, :ca, :aj)
                   ON CONFLICT (license_key) DO UPDATE SET
                       access_token  = EXCLUDED.access_token,
                       refresh_token = EXCLUDED.refresh_token,
                       token_type    = EXCLUDED.token_type,
                       expires_at    = EXCLUDED.expires_at,
                       connected_at  = EXCLUDED.connected_at,
                       accounts_json = EXCLUDED.accounts_json"""
            ),
            {
                "uid": user_id,
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
def deriv_status(user_id: str = Query(...)):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT expires_at, connected_at, accounts_json, refresh_token "
                "FROM deriv_connections WHERE license_key = :uid"
            ),
            {"uid": user_id},
        ).fetchone()
    if row is None:
        return {"connected": False}
    # token_valid is true if the access token is fresh OR we can refresh it
    token_valid = row[0] > int(time.time()) or bool(row[3])
    return {
        "connected": True,
        "token_valid": token_valid,
        "connected_at": row[1],
        "accounts": row[2],
    }


@router.delete("/disconnect")
def deriv_disconnect(user_id: str = Query(...)):
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM deriv_connections WHERE license_key = :uid"),
            {"uid": user_id},
        )
    return {"disconnected": True}


# ------------------------------------------------- HELPER FOR THE WORKER ---
def _refresh_deriv_token(user_id: str, refresh_token: str) -> str | None:
    """Exchange the stored refresh_token for a fresh access token.
    Returns the new access token, or None if Deriv refuses (user must
    reconnect via the normal OAuth login)."""
    try:
        resp = httpx.post(
            DERIV_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": DERIV_CLIENT_ID,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
    except httpx.HTTPError:
        return None

    if resp.status_code != 200:
        return None

    tok = resp.json()
    access_token = tok.get("access_token")
    if not access_token:
        return None

    new_expires_at = int(time.time()) + int(tok.get("expires_in", 3600))
    # Deriv may rotate the refresh token — keep the newest one we have
    new_refresh = tok.get("refresh_token") or refresh_token

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE deriv_connections SET "
                "access_token = :at, refresh_token = :rt, expires_at = :ea "
                "WHERE license_key = :uid"
            ),
            {"at": access_token, "rt": new_refresh, "ea": new_expires_at,
             "uid": user_id},
        )
    return access_token


def get_deriv_token(user_id: str) -> str | None:
    """Return a valid access token for this user, silently refreshing it
    if it has expired (or expires within the next 60 seconds).
    Returns None only when no connection exists or refresh is impossible —
    in that case the user must reconnect their Deriv account."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT access_token, refresh_token, expires_at "
                "FROM deriv_connections WHERE license_key = :uid"
            ),
            {"uid": user_id},
        ).fetchone()

    if row is None:
        return None

    access_token, refresh_token, expires_at = row[0], row[1], row[2]

    # Still valid (with a 60s safety margin)? Use it as-is.
    if expires_at > int(time.time()) + 60:
        return access_token

    # Expired — try a silent refresh.
    if refresh_token:
        return _refresh_deriv_token(user_id, refresh_token)

    # No refresh token stored (older connection) — reconnect required.
    return None


# ------------------------------------------------------------ RESULT PAGE ---
def _result_page(ok: bool, msg: str) -> str:
    """NolimitzBots black/gold themed result page.
    On success: auto-redirects back to the website after 3 seconds."""
    color = "#22c55e" if ok else "#ef4444"
    icon = "&#10003;" if ok else "&#10007;"
    title = "Connected!" if ok else "Connection failed"
    redirect = (
        f'<meta http-equiv="refresh" content="3;url={WEBSITE_URL}">' if ok else ""
    )
    note = "Taking you back to NolimitzBots&hellip;" if ok else msg
    return f"""<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NolimitzBots</title>{redirect}<style>
  body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
       background:linear-gradient(135deg,#0a0804,#171204);font-family:-apple-system,
       Segoe UI,Roboto,sans-serif;color:#f5efdc}}
  .card{{background:rgba(255,215,120,.05);border:1px solid rgba(212,175,55,.35);
       backdrop-filter:blur(18px);border-radius:20px;padding:40px 32px;text-align:center;
       max-width:340px;box-shadow:0 20px 60px rgba(0,0,0,.55)}}
  .brand{{font-size:13px;letter-spacing:2px;color:#d4af37;font-weight:700;
       margin-bottom:16px;text-transform:uppercase}}
  .icon{{width:64px;height:64px;border-radius:50%;background:{color}22;color:{color};
       display:flex;align-items:center;justify-content:center;font-size:30px;
       margin:0 auto 18px;border:2px solid {color}}}
  h1{{font-size:20px;margin:0 0 8px;color:#fff}}
  p{{opacity:.7;font-size:14px;margin:0 0 22px}}
  .btn{{display:inline-block;background:#c9a227;color:#141005;text-decoration:none;
       padding:12px 28px;border-radius:12px;font-weight:700;font-size:14px}}
</style></head><body>
  <div class="card">
    <div class="brand">NolimitzBots AI</div>
    <div class="icon">{icon}</div>
    <h1>{title}</h1>
    <p>{note}</p>
    <a class="btn" href="{WEBSITE_URL}">Back to NolimitzBots</a>
  </div>
</body></html>"""