from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api import dependencies
from app.core import security, config
from app.db import models
from app.schemas import user as user_schema
import uuid

router = APIRouter()

@router.post("/register", response_model=user_schema.User)
async def register_user(user_in: user_schema.UserCreate, db: AsyncSession = Depends(dependencies.get_db)):
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

@router.post("/login", response_model=user_schema.Token)
async def login_access_token(db: AsyncSession = Depends(dependencies.get_db), form_data: OAuth2PasswordRequestForm = Depends()):
    result = await db.execute(select(models.User).filter(models.User.email == form_data.username))
    user = result.scalars().first()
    if not user or not user.password:
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    if not security.verify_password(form_data.password, user.password):
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    
    access_token_expires = timedelta(minutes=config.settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        subject=user.email, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

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
    for field, value in update_data.items():
        if field == "password":
            setattr(current_user, field, security.get_password_hash(value))
        else:
            setattr(current_user, field, value)
    
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return current_user
