import uuid
import time
import logging
import sqlalchemy
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.api import api_router
from app.services.cleanup import run_cleanup
from app.core.config import settings
from app.db.database import engine, Base
from app.middleware.rate_limit import limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Only use create_all in development; production should use alembic migrations
    if settings.ENVIRONMENT != "production":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # Run startup cleanup to repair any inconsistent rows
    from app.db.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await run_cleanup(db)

    yield

    # Shutdown: close shared HTTP client
    from app.services.media_service import _shared_client
    if _shared_client and not _shared_client.is_closed:
        await _shared_client.aclose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# --- CORS ---
# CSRF protection is achieved through the combination of:
#   1. CORS origin allowlist — blocks cross-origin requests from other sites
#   2. SameSite=Strict auth cookies — browser won't attach cookies on cross-origin
#   3. Rate limiting on auth endpoints — limits brute-force attempts
# This is the recommended pattern for API backends consumed by a separate frontend.
origins = [o.strip() for o in settings.FRONTEND_URL.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
)


# --- Request ID middleware ---
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# --- Request timing + structured logging ---
@app.middleware("http")
async def add_timing(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    response.headers["X-Response-Time"] = f"{duration:.3f}s"
    request_id = getattr(request.state, "request_id", "-")
    logger.info(
        "%s %s %d %s [%s]",
        request.method,
        request.url.path,
        response.status_code,
        f"{duration:.3f}s",
        request_id,
    )
    if duration > 1.0:
        logger.warning("Slow request: %s %s took %.2fs", request.method, request.url.path, duration)
    return response


# --- Global exception handler ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error("Unhandled exception [%s]: %s", request_id, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
    )


# --- Security headers ---
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' https://image.tmdb.org https: data:; "
        "connect-src 'self' https://api.themoviedb.org https://api.tvmaze.com https://api.jikan.moe https://api.deepseek.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "frame-ancestors 'none'"
    )
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/")
def root():
    return {"message": "Welcome to Things Ive Watched API"}


@app.get("/health")
async def health():
    checks = {}
    ok = True

    # Database
    try:
        async with engine.connect() as conn:
            await conn.execute(sqlalchemy.text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "failed"
        ok = False
        logger.error("Health check DB failed", exc_info=True)

    # TMDB API (non-blocking, degraded is acceptable)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(
                f"https://api.themoviedb.org/3/movie/popular",
                params={"api_key": settings.TMDB_API_KEY, "page": 1},
            )
            checks["tmdb"] = "ok" if resp.status_code == 200 else "degraded"
    except Exception:
        checks["tmdb"] = "degraded"

    status_code = 200 if ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "healthy" if ok else "unhealthy", "checks": checks},
    )
