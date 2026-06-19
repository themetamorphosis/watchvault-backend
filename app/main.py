import uuid
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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Run startup cleanup to repair any inconsistent rows
    from app.db.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await run_cleanup(db)

    yield


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
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
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
    try:
        async with engine.connect() as conn:
            await conn.execute(sqlalchemy.text("SELECT 1"))
        return {"status": "ok", "db": "connected"}
    except Exception:
        logger.error("Health check failed", exc_info=True)
        return JSONResponse(status_code=503, content={"status": "unhealthy", "db": "connection failed"})
