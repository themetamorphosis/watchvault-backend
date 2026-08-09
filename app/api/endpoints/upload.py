import logging
import os
import time

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

from app.api import dependencies
from app.db import models
from app.utils.file_validator import (
    extension_for_content,
    read_upload_capped,
    validate_upload_file,
)
from app.utils.upload_paths import (
    AVATARS_SUBDIR,
    local_path_for_url,
    public_url,
    upload_subdir,
)
import aiofiles

router = APIRouter()
logger = logging.getLogger(__name__)

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
    # Derived from the validated bytes, never from the client's filename.
    ext = extension_for_content(content)
    filename = f"{current_user.id}-{int(time.time() * 1000)}.{ext}"
    filepath = os.path.join(upload_dir, filename)

    async with aiofiles.open(filepath, "wb") as out_file:
        await out_file.write(content)

    image_url = public_url(AVATARS_SUBDIR, filename)

    # Capture the outgoing avatar before the row is rewritten.
    previous_url = current_user.image

    stmt = update(models.User).where(models.User.id == current_user.id).values(image=image_url)
    await db.execute(stmt)
    await db.commit()

    # Only after the new avatar is committed: every superseded upload used to
    # stay on disk forever, still publicly reachable. Deleting is best-effort —
    # a failure here must not fail an upload that already succeeded.
    if previous_url != image_url:
        stale_path = local_path_for_url(previous_url, AVATARS_SUBDIR)
        if stale_path:
            try:
                os.remove(stale_path)
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning("Could not remove superseded avatar %s", stale_path, exc_info=True)

    return {"success": True, "imageUrl": image_url}
