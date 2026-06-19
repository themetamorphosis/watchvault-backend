import os
import time

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import dependencies
from app.core.config import settings
from app.db import models
from app.utils.file_validator import validate_upload_file, get_upload_dir, sanitize_extension
import aiofiles

router = APIRouter()

MAX_SIZE = 5 * 1024 * 1024  # 5MB


def _get_snapshot_dir() -> str:
    base = settings.UPLOAD_DIR or os.path.join(os.getcwd(), "uploads", "snapshots")
    return get_upload_dir(base)


@router.post("")
async def upload_snapshot(
    file: UploadFile = File(...),
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    content = await file.read()
    validate_upload_file(file, content, MAX_SIZE)

    upload_dir = _get_snapshot_dir()
    ext = sanitize_extension(file.filename, default="png")
    filename = f"dash-{current_user.id}-{int(time.time() * 1000)}.{ext}"
    filepath = os.path.join(upload_dir, filename)

    async with aiofiles.open(filepath, "wb") as out_file:
        await out_file.write(content)

    image_url = f"/uploads/snapshots/{filename}"

    return {"success": True, "imageUrl": image_url}
