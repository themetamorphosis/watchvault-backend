import os
import time
import magic

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

from app.api import dependencies
from app.core.config import settings
from app.db import models
import aiofiles

router = APIRouter()

ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"]
ALLOWED_MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"RIFF": "image/webp",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}
MAX_SIZE = 10 * 1024 * 1024  # 10MB


def _get_upload_dir() -> str:
    base = settings.UPLOAD_DIR or os.path.join(os.getcwd(), "uploads", "avatars")
    os.makedirs(base, exist_ok=True)
    return base


def _validate_magic_bytes(content: bytes) -> bool:
    for sig in ALLOWED_MAGIC:
        if content[:len(sig)] == sig:
            return True
    return False


@router.post("")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Invalid file type. Use JPEG, PNG, WebP, or GIF.")

    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)

    if size > MAX_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max 10MB.")

    content = await file.read()

    if not _validate_magic_bytes(content):
        raise HTTPException(status_code=400, detail="File content does not match an allowed image format.")

    upload_dir = _get_upload_dir()
    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "jpg"
    ext = "".join(c for c in ext if c.isalnum())[:10]
    filename = f"{current_user.id}-{int(time.time() * 1000)}.{ext}"
    filepath = os.path.join(upload_dir, filename)

    async with aiofiles.open(filepath, "wb") as out_file:
        await out_file.write(content)

    image_url = f"/uploads/avatars/{filename}"

    stmt = update(models.User).where(models.User.id == current_user.id).values(image=image_url)
    await db.execute(stmt)
    await db.commit()

    return {"success": True, "imageUrl": image_url}
