from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional
from datetime import datetime


# bcrypt hashes at most 72 bytes and raises on anything longer, so the ceiling
# is a byte count, not a character count: "é" * 40 is 40 characters but 80
# bytes. Without this the ValueError escaped as a 500 instead of a 422.
MAX_PASSWORD_BYTES = 72


def _validate_password(v: str) -> str:
    if len(v.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes "
            "(non-ASCII characters count as more than one)"
        )
    has_digit = any(c.isdigit() for c in v)
    has_special = any(not c.isalnum() for c in v)
    if not (has_digit or has_special):
        raise ValueError("Password must contain at least one digit or special character")
    return v


class UserBase(BaseModel):
    email: EmailStr
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    image: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        return _validate_password(v)


class UserUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    image: Optional[str] = None
    password: Optional[str] = Field(None, min_length=8)
    # Required by the endpoint whenever `password` is present. Declared here so
    # it survives model_dump(exclude_unset=True) and never reaches setattr.
    current_password: Optional[str] = Field(None, min_length=1)

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_password(v)


class User(UserBase):
    id: str
    emailVerified: Optional[datetime] = None
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str


class TokenRefresh(BaseModel):
    refresh_token: str


class TokenData(BaseModel):
    email: Optional[str] = None
