from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional
from datetime import datetime


class UserBase(BaseModel):
    email: EmailStr
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    image: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        has_digit = any(c.isdigit() for c in v)
        has_special = any(not c.isalnum() for c in v)
        if not (has_digit or has_special):
            raise ValueError("Password must contain at least one digit or special character")
        return v


class UserUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    image: Optional[str] = None
    password: Optional[str] = Field(None, min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        has_digit = any(c.isdigit() for c in v)
        has_special = any(not c.isalnum() for c in v)
        if not (has_digit or has_special):
            raise ValueError("Password must contain at least one digit or special character")
        return v


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
