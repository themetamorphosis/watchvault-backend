from datetime import datetime, timedelta, timezone
from typing import Any, Union
from jose import jwt, JWTError
import bcrypt
from app.core.config import settings

ALGORITHM = "HS256"
TOKEN_TYPE_KEY = "type"

# bcrypt refuses inputs over 72 bytes rather than truncating them.
BCRYPT_MAX_BYTES = 72


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a password against a hash.

    An over-long candidate is a failed match, not an error: no hash this
    function ever produced could have come from one. Returning False keeps the
    ValueError out of the login path, which takes its input from
    OAuth2PasswordRequestForm and so never passes through Pydantic validation.
    """
    encoded = plain_password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        return False
    return bcrypt.checkpw(encoded, hashed_password.encode("utf-8"))


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_access_token(subject: Union[str, Any], expires_delta: timedelta = None) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = {"exp": expire, "sub": str(subject), TOKEN_TYPE_KEY: "access"}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(subject: Union[str, Any], family: str = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode = {"exp": expire, "sub": str(subject), TOKEN_TYPE_KEY: "refresh"}
    if family:
        to_encode["family"] = family
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token. Returns payload or raises JWTError."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])


def verify_token_type(payload: dict, expected_type: str) -> bool:
    """Verify the token type claim matches what we expect."""
    return payload.get(TOKEN_TYPE_KEY) == expected_type
