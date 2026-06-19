import os
from slowapi import Limiter
from fastapi import Request

_TRUSTED_PROXIES = {
    ip.strip()
    for ip in os.environ.get("TRUSTED_PROXY_IPS", "").split(",")
    if ip.strip()
}


def _get_remote_address(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For only from trusted proxies."""
    client_ip = request.client.host if request.client else "unknown"
    if _TRUSTED_PROXIES and client_ip in _TRUSTED_PROXIES:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return client_ip


limiter = Limiter(key_func=_get_remote_address, default_limits=["60/minute"])
