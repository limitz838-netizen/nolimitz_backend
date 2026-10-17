"""
app/security_guard.py -- keeps scanners and the 17 Oct intruder out (Oct 2026)

Installed from app/main.py with two lines:
    from app.security_guard import install_security_guard
    install_security_guard(app)

What it does, before any route runs:
  1. Blocked IPs     -> 403. Built-in list of the intruder's addresses plus
                        anything in the BLOCKED_IPS env var (comma separated).
  2. Admin signup    -> 403 unless ADMIN_SIGNUP_OPEN=true. You are the only
                        admin; open signup is how the intruder got accounts.
  3. Admin login     -> 5 failed logins from one IP in 15 min = that IP is
                        blocked for 1 hour (429).
  4. Probing         -> 30 refused calls (401/404/405) to admin, worker,
                        copier or internal routes from one IP in 10 min =
                        blocked for 1 hour (429). Customers using the app do
                        not touch these routes, so they are not affected.
  Never blocked: the VPS (80.241.217.13) and anything in SAFE_IPS.

Nothing is stored; blocks reset on a deploy. Every block is logged.
"""
import logging
import os
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("security_guard")

KNOWN_BAD = {"154.118.0.98", "154.118.35.19", "13.135.163.240"}
BLOCKED = KNOWN_BAD | {i.strip() for i in os.getenv("BLOCKED_IPS", "").split(",") if i.strip()}
SAFE = {"80.241.217.13"} | {i.strip() for i in os.getenv("SAFE_IPS", "").split(",") if i.strip()}
SIGNUP_OPEN = os.getenv("ADMIN_SIGNUP_OPEN", "").strip().lower() == "true"

LOGIN_FAILS, LOGIN_WINDOW = 5, 15 * 60
PROBE_FAILS, PROBE_WINDOW = 30, 10 * 60
BLOCK_SEC = 60 * 60

SENSITIVE = ("/admin", "/api/admin", "/api/worker", "/worker", "/client/worker",
             "/copier", "/payments/jarvis", "/payments/telegram-upgrade",
             "/entitlement", "/reminders", "/eas", "/docs", "/openapi.json", "/redoc")

_login = defaultdict(deque)
_probe = defaultdict(deque)
_until = {}


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _hit(store, ip, window, limit, now):
    q = store[ip]
    q.append(now)
    while q and q[0] < now - window:
        q.popleft()
    return len(q) >= limit


def install_security_guard(app):
    @app.middleware("http")
    async def security_guard(request: Request, call_next):
        ip = _ip(request)
        path = request.url.path
        now = time.time()

        if ip not in SAFE:
            if ip in BLOCKED:
                return JSONResponse({"detail": "Forbidden"}, status_code=403)
            if _until.get(ip, 0) > now:
                return JSONResponse({"detail": "Too many requests"}, status_code=429)
            if path.rstrip("/") == "/admin/signup" and request.method == "POST" and not SIGNUP_OPEN:
                log.warning("admin signup refused from %s", ip)
                return JSONResponse({"detail": "Admin signup is closed"}, status_code=403)

        response = await call_next(request)

        if ip and ip not in SAFE:
            code = response.status_code
            if path.rstrip("/") == "/admin/login" and code in (401, 403):
                if _hit(_login, ip, LOGIN_WINDOW, LOGIN_FAILS, now):
                    _until[ip] = now + BLOCK_SEC
                    log.warning("blocked %s for 1h: %d failed admin logins", ip, LOGIN_FAILS)
            elif code in (401, 404, 405) and path.startswith(SENSITIVE):
                if _hit(_probe, ip, PROBE_WINDOW, PROBE_FAILS, now):
                    _until[ip] = now + BLOCK_SEC
                    log.warning("blocked %s for 1h: probing (%d refused calls)", ip, PROBE_FAILS)
        return response
