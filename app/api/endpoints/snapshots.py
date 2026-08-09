import os
import time

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import dependencies
from app.db import models
from app.utils.file_validator import (
    extension_for_content,
    read_upload_capped,
    validate_upload_file,
)
from app.utils.upload_paths import SNAPSHOTS_SUBDIR, public_url, upload_subdir
import aiofiles

router = APIRouter()

MAX_SIZE = 5 * 1024 * 1024  # 5MB


@router.post("")
async def upload_snapshot(
    file: UploadFile = File(...),
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    content = await read_upload_capped(file, MAX_SIZE)
    validate_upload_file(file, content, MAX_SIZE)

    upload_dir = upload_subdir(SNAPSHOTS_SUBDIR)
    # Derived from the validated bytes, never from the client's filename.
    ext = extension_for_content(content, default="png")
    filename = f"dash-{current_user.id}-{int(time.time() * 1000)}.{ext}"
    filepath = os.path.join(upload_dir, filename)

    async with aiofiles.open(filepath, "wb") as out_file:
        await out_file.write(content)

    image_url = public_url(SNAPSHOTS_SUBDIR, filename)

    return {"success": True, "imageUrl": image_url}
