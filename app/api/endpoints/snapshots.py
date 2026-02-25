import os
import time
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api import dependencies
from app.db import models
import aiofiles

router = APIRouter()

ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"]
MAX_SIZE = 5 * 1024 * 1024 # 5MB for dashboard snaps

UPLOAD_DIR = os.path.normpath(os.path.join(os.getcwd(), "..", "thingsivewatched", "public", "uploads", "snapshots"))

@router.post("")
async def upload_snapshot(
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
        raise HTTPException(status_code=400, detail="File too large. Max 5MB.")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    
    ext = file.filename.split(".")[-1] if "." in file.filename else "png"
    filename = f"dash-{current_user.id}-{int(time.time()*1000)}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    async with aiofiles.open(filepath, 'wb') as out_file:
        content = await file.read()
        await out_file.write(content)

    image_url = f"/uploads/snapshots/{filename}"
    
    # We do NOT update the user's profile image here. 
    # Just return the public URL so the frontend can copy it to the clipboard.

    return {"success": True, "imageUrl": image_url}
