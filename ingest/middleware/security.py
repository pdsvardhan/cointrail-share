"""
Security middleware: CORS lockdown + API key authentication.

iter-18 CH3: authentication FAILS CLOSED. Until iter-18 a missing INGEST_API_KEY only printed a
warning and served the whole financial database unauthenticated — on a host that Cloudflare
publishes to the internet. Now a missing key refuses every non-public request with 503 (the
server is misconfigured; the caller is not at fault) unless INGEST_ALLOW_NO_AUTH=1 is set
explicitly, which is for local development only.
"""
import os
import secrets
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


# Endpoints that skip auth
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

# A misconfigured server is refused on every request; log it at most this often so the reason is
# always in the recent log without flooding it (the forwarder retries each SMS 10 times).
MISCONFIG_LOG_INTERVAL_S = 60


def setup_cors(app: FastAPI):
    """Configure CORS with explicit allowed origins."""
    raw = os.environ.get("ALLOWED_ORIGINS", "")
    if raw:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
    else:
        origins = [
            "https://cointrail.example.com",
            "http://cointrail:8501",
            "http://localhost:3000",
            "http://localhost:8501",
        ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


class APIKeyMiddleware(BaseHTTPMiddleware):
    """api_key=None means the key is missing: fail closed (503) for everything non-public."""

    def __init__(self, app, api_key):
        super().__init__(app)
        self.api_key = api_key
        self._last_misconfig_log = 0.0

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public paths
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Skip auth for OPTIONS (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        if not self.api_key:
            now = time.monotonic()
            if now - self._last_misconfig_log >= MISCONFIG_LOG_INTERVAL_S:
                self._last_misconfig_log = now
                print(f"[AUTH] REFUSING {request.method} {request.url.path}: INGEST_API_KEY is not set "
                      f"(fail closed). Set it in .env and restart.")
            return JSONResponse(
                status_code=503,
                content={"detail": "Server misconfigured: API key not set; refusing all requests"},
            )

        # Check API key (timing-safe comparison)
        provided = request.headers.get("X-API-Key", "")
        if not provided or not secrets.compare_digest(provided, self.api_key):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"}
            )

        return await call_next(request)


def setup_auth(app: FastAPI):
    """Require X-API-Key on every non-public path. Missing key -> fail closed (iter-18 CH3)."""
    api_key = os.environ.get("INGEST_API_KEY", "").strip()
    if api_key:
        app.add_middleware(APIKeyMiddleware, api_key=api_key)
        return
    if os.environ.get("INGEST_ALLOW_NO_AUTH", "").strip() == "1":
        print("=" * 72)
        print("WARNING: INGEST_API_KEY is not set and INGEST_ALLOW_NO_AUTH=1 —")
        print("the API is UNAUTHENTICATED. Development only; never on the server.")
        print("=" * 72)
        return
    print("=" * 72)
    print("ERROR: INGEST_API_KEY is not set. Failing closed: every request except")
    print(f"{sorted(PUBLIC_PATHS)} gets 503 until the key is configured.")
    print("(Local development without a key: set INGEST_ALLOW_NO_AUTH=1.)")
    print("=" * 72)
    app.add_middleware(APIKeyMiddleware, api_key=None)


def generate_api_key():
    """Generate a random API key (run once, save to .env)."""
    return secrets.token_urlsafe(32)
