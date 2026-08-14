from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.api import dependencies
from app.core import security, config
from app.db import models
from app.middleware.rate_limit import limiter
from app.schemas import user as user_schema
import uuid

router = APIRouter()


@router.post("/register", response_model=user_schema.User)
@limiter.limit("3/minute")
async def register_user(request: Request, user_in: user_schema.UserCreate, db: AsyncSession = Depends(dependencies.get_db)):
    result = await db.execute(select(models.User).filter(models.User.email == user_in.email))
    user = result.scalars().first()
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this user email already exists in the system.",
        )
    user_id = str(uuid.uuid4())
    db_obj = models.User(
        id=user_id,
        email=user_in.email,
        password=security.get_password_hash(user_in.password),
        name=user_in.name,
        image=user_in.image
    )
    db.add(db_obj)
    await db.commit()
    await db.refresh(db_obj)
    return db_obj


# A real bcrypt hash to compare against when the account doesn't exist. Without
# it the miss path returns before hashing, and the response-time difference
# tells an attacker which emails are registered.
_DUMMY_PASSWORD_HASH = security.get_password_hash("not-a-real-password-placeholder")


@router.post("/login", response_model=user_schema.Token)
@limiter.limit("5/minute")
async def login_access_token(request: Request, db: AsyncSession = Depends(dependencies.get_db), form_data: OAuth2PasswordRequestForm = Depends()):
    result = await db.execute(select(models.User).filter(models.User.email == form_data.username))
    user = result.scalars().first()

    # Always run one comparison, whether or not the account exists, so both
    # paths cost the same.
    stored_hash = user.password if user and user.password else _DUMMY_PASSWORD_HASH
    password_ok = security.verify_password(form_data.password, stored_hash)

    if not user or not user.password or not password_ok:
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    # Issue new token family on login (invalidates any existing refresh tokens)
    user.token_family = str(uuid.uuid4())
    await db.commit()

    access_token_expires = timedelta(minutes=config.settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        subject=user.email, expires_delta=access_token_expires
    )
    refresh_token = security.create_refresh_token(subject=user.email, family=user.token_family)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/refresh", response_model=user_schema.Token)
@limiter.limit("10/minute")
async def refresh_access_token(request: Request, db: AsyncSession = Depends(dependencies.get_db)):
    """Exchange a valid refresh token for a new access + refresh token pair."""
    body = await request.json()
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token is required")

    try:
        payload = security.decode_token(refresh_token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if not security.verify_token_type(payload, "refresh"):
        raise HTTPException(status_code=401, detail="Invalid token type")

    email = payload.get("sub")
    token_family = payload.get("family")
    if not email:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # Verify user still exists and token family matches
    result = await db.execute(select(models.User).filter(models.User.email == email))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")

    if not token_family or user.token_family != token_family:
        # Family mismatch = replay attack or revoked token
        # Invalidate the entire family as a safety measure
        user.token_family = None
        await db.commit()
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")

    # Rotate: new family invalidates this and all other tokens in the old family
    user.token_family = str(uuid.uuid4())
    await db.commit()

    access_token_expires = timedelta(minutes=config.settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    new_access = security.create_access_token(subject=email, expires_delta=access_token_expires)
    new_refresh = security.create_refresh_token(subject=email, family=user.token_family)

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }


@router.post("/logout")
async def logout(
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db)
):
    """Revoke all refresh tokens for this user by clearing their token family."""
    current_user.token_family = None
    await db.commit()
    return {"ok": True}


@router.get("/me", response_model=user_schema.User)
async def read_current_user(current_user: models.User = Depends(dependencies.get_current_user)):
    return current_user


@router.patch("/me", response_model=user_schema.User)
async def update_current_user(
    user_in: user_schema.UserUpdate,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db)
):
    update_data = user_in.model_dump(exclude_unset=True)

    # `current_password` is a credential, not a column — pull it out before the
    # setattr loop so it can never be written to the model.
    current_password = update_data.pop("current_password", None)

    if "password" in update_data:
        if not current_password:
            raise HTTPException(
                status_code=400,
                detail="current_password is required to change your password",
            )
        if not current_user.password or not security.verify_password(
            current_password, current_user.password
        ):
            raise HTTPException(status_code=400, detail="Current password is incorrect")

    for field, value in update_data.items():
        if field == "password":
            setattr(current_user, field, security.get_password_hash(value))
        else:
            setattr(current_user, field, value)

    if "password" in update_data:
        # Every refresh token issued before the change must die with it —
        # otherwise changing a password after a compromise locks nobody out.
        current_user.token_family = None

    db.add(current_user)
    try:
        await db.commit()
    except IntegrityError:
        # `handle` is globally unique. Two people can claim the same one at the
        # same moment, so the index is the arbiter rather than a pre-check.
        await db.rollback()
        raise HTTPException(status_code=409, detail="That handle is already taken")
    await db.refresh(current_user)
    return current_user
