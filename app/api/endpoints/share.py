import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import dependencies
from app.db import models
from app.schemas import share as share_schema

router = APIRouter()

# A personal tracker has no use for hundreds of links, and each one is an
# anonymous read path into the library. Capping keeps that surface bounded.
MAX_SHARE_LINKS = 20


@router.get("", response_model=share_schema.ShareLinkList)
async def list_share_links(
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    result = await db.execute(
        select(models.ShareLink)
        .filter(models.ShareLink.userId == current_user.id)
        .order_by(models.ShareLink.createdAt.desc())
    )
    return {"links": result.scalars().all()}


@router.post("", response_model=share_schema.ShareLink, status_code=201)
async def create_share_link(
    link_in: share_schema.ShareLinkCreate,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    count = (
        await db.execute(
            select(func.count())
            .select_from(models.ShareLink)
            .where(models.ShareLink.userId == current_user.id)
        )
    ).scalar()
    if count >= MAX_SHARE_LINKS:
        raise HTTPException(
            status_code=400,
            detail=f"You can have at most {MAX_SHARE_LINKS} share links. Revoke one first.",
        )

    db_link = models.ShareLink(
        id=str(uuid.uuid4()),
        userId=current_user.id,
        **link_in.model_dump(),
    )
    db.add(db_link)
    try:
        await db.commit()
    except IntegrityError:
        # Slugs are global, so the conflict is usually with a *different*
        # user's link. The unique index is the arbiter; a pre-check SELECT
        # would still race.
        await db.rollback()
        raise HTTPException(status_code=409, detail="That handle is already taken")
    await db.refresh(db_link)
    return db_link


async def _get_owned_link(
    link_id: str, user_id: str, db: AsyncSession
) -> models.ShareLink:
    result = await db.execute(
        select(models.ShareLink).filter(
            models.ShareLink.id == link_id,
            models.ShareLink.userId == user_id,
        )
    )
    link = result.scalars().first()
    if not link:
        # 404 rather than 403 for a link owned by someone else: confirming it
        # exists would tell a caller which ids are real.
        raise HTTPException(status_code=404, detail="Share link not found")
    return link


@router.patch("/{link_id}", response_model=share_schema.ShareLink)
async def update_share_link(
    link_id: str,
    link_in: share_schema.ShareLinkUpdate,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    db_link = await _get_owned_link(link_id, current_user.id, db)

    for field, value in link_in.model_dump(exclude_unset=True).items():
        setattr(db_link, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="That handle is already taken")
    await db.refresh(db_link)
    return db_link


@router.delete("/{link_id}")
async def delete_share_link(
    link_id: str,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    """Revoke a link. Deleting the row is the only revocation mechanism, so the
    URL stops resolving immediately and the handle is free to reuse."""
    db_link = await _get_owned_link(link_id, current_user.id, db)
    await db.delete(db_link)
    await db.commit()
    return {"ok": True}
