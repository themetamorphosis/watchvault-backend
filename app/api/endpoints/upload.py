import os
import time
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update
from app.api import dependencies
from app.db import models
import aiofiles

router = APIRouter()

ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"]
MAX_SIZE = 2 * 1024 * 1024 # 2MB

# Ensure upload directory exists relative to backend
# We will save it in thingsivewatched/public/uploads/avatars so it's accessible by next.js natively
# Wait, frontend is in another folder. We'll find thingsivewatched folder dynamically
UPLOAD_DIR = os.path.normpath(os.path.join(os.getcwd(), "..", "thingsivewatched", "public", "uploads", "avatars"))

@router.post("")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db)
):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Invalid file type. Use JPEG, PNG, WebP, or GIF.")

    # Reading file size efficiently
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)
    
    if size > MAX_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max 2MB.")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    
    ext = file.filename.split(".")[-1] if "." in file.filename else "jpg"
    filename = f"{current_user.id}-{int(time.time()*1000)}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    async with aiofiles.open(filepath, 'wb') as out_file:
        content = await file.read()
        await out_file.write(content)

    image_url = f"/uploads/avatars/{filename}"
    
    stmt = update(models.User).where(models.User.id == current_user.id).values(image=image_url)
    await db.execute(stmt)
    await db.commit()

    return {"success": True, "imageUrl": image_url}
