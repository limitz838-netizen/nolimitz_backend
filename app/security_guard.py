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
  3. Admin login     -> 5 failed logins (/admin/login or /api/ai/login) from
                        one IP in 15 min = that IP is blocked for 1 hour (429).
  4. Probing         -> 30 refused calls (401/404/405) to admin, worker,
                        copier or internal routes from one IP in 10 min =
                        blocked for 1 hour (429). Customers using the app do
                        not touch these routes, so they are not affected.
  Never blocked: the VPS (80.241.217.13) and anything in SAFE_IPS.

Nothing is stored; blocks reset on a deploy. Every block is logged.

  5. Memory watch   -> every 2 s it reads the process memory. Above
                       MEMWATCH_MB (default 700 MB, normal is ~250 MB) it
                       logs every request still running (method, path, IP,
                       seconds so far), so if one request blows the 2 GB limit
                       we see which one it was before the restart wipes it.
"""
import logging
import os
import threading
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

MEMWATCH_MB = int(os.getenv("MEMWATCH_MB", "700"))
_inflight = {}
_inflight_lock = threading.Lock()


def _rss_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        return 0
    return 0


def _memwatch():
    last_log = 0.0
    while True:
        time.sleep(2)
        mb = _rss_mb()
        if mb >= MEMWATCH_MB and time.time() - last_log > 10:
            last_log = time.time()
            now = time.time()
            with _inflight_lock:
                rows = sorted(_inflight.values(), key=lambda r: r[0])
            running = "; ".join(f"{m} {p} from {ip} for {now - t0:.0f}s" for t0, m, p, ip in rows[:15])
            # print, not log: always lands in Render logs whatever the log level
            print(f"[memwatch] process at {mb} MB. In-flight requests: {running or 'none'}", flush=True)


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
    if MEMWATCH_MB > 0:
        threading.Thread(target=_memwatch, name="memwatch", daemon=True).start()

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

        key = id(request)
        with _inflight_lock:
            _inflight[key] = (now, request.method, path, ip)
        try:
            response = await call_next(request)
        finally:
            with _inflight_lock:
                _inflight.pop(key, None)

        if ip and ip not in SAFE:
            code = response.status_code
            if path.rstrip("/") in ("/admin/login", "/api/ai/login") and code in (401, 403):
                if _hit(_login, ip, LOGIN_WINDOW, LOGIN_FAILS, now):
                    _until[ip] = now + BLOCK_SEC
                    log.warning("blocked %s for 1h: %d failed admin logins", ip, LOGIN_FAILS)
            elif code in (401, 404, 405) and path.startswith(SENSITIVE):
                if _hit(_probe, ip, PROBE_WINDOW, PROBE_FAILS, now):
                    _until[ip] = now + BLOCK_SEC
                    log.warning("blocked %s for 1h: probing (%d refused calls)", ip, PROBE_FAILS)
        return response
