import os
import time

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

from app.api import dependencies
from app.db import models
from app.utils.file_validator import (
    read_upload_capped,
    sanitize_extension,
    validate_upload_file,
)
from app.utils.upload_paths import AVATARS_SUBDIR, public_url, upload_subdir
import aiofiles

router = APIRouter()

MAX_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    content = await read_upload_capped(file, MAX_SIZE)
    validate_upload_file(file, content, MAX_SIZE)

    upload_dir = upload_subdir(AVATARS_SUBDIR)
    ext = sanitize_extension(file.filename)
    filename = f"{current_user.id}-{int(time.time() * 1000)}.{ext}"
    filepath = os.path.join(upload_dir, filename)

    async with aiofiles.open(filepath, "wb") as out_file:
        await out_file.write(content)

    image_url = public_url(AVATARS_SUBDIR, filename)

    stmt = update(models.User).where(models.User.id == current_user.id).values(image=image_url)
    await db.execute(stmt)
    await db.commit()

    return {"success": True, "imageUrl": image_url}
