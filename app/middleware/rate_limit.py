from slowapi import Limiter
from fastapi import Request


def _get_remote_address(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind a reverse proxy.

    Only trusts the header when TRUSTED_PROXY_IPS is non-empty (set via env var).
    Falls back to the direct connection IP otherwise.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Take the first (leftmost) IP which is the original client
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=_get_remote_address, default_limits=["60/minute"])
