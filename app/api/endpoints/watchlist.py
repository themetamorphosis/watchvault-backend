from typing import List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api import dependencies
from app.db import models
from app.schemas import watchlist as watchlist_schema
from app.api.endpoints.media import get_cached, enrich_media_cache
from app.db.database import AsyncSessionLocal
from fastapi.responses import JSONResponse
import uuid
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


async def _background_enrich(title: str, media_type: str, year: int | None, item_id: str):
    """Background task: enrich MediaCache and backfill the WatchlistItem."""
    async with AsyncSessionLocal() as db:
        try:
            await enrich_media_cache(db, title, media_type, year)

            # Backfill the watchlist item from the cache
            cached = await get_cached(db, title, media_type, year)
            if cached:
                result = await db.execute(
                    select(models.WatchlistItem).filter(models.WatchlistItem.id == item_id)
                )
                item = result.scalars().first()
                if item:
                    changed = False
                    if not item.coverUrl and cached.coverUrl:
                        item.coverUrl = cached.coverUrl
                        changed = True
                    if (not item.genres or len(item.genres) == 0) and cached.genres and len(cached.genres) > 0:
                        item.genres = cached.genres
                        changed = True
                    if item.runtime is None and cached.runtime is not None:
                        item.runtime = cached.runtime
                        changed = True
                    if not item.description and cached.description:
                        item.description = cached.description
                        changed = True
                    if changed:
                        await db.commit()
                        logger.info(f"Background enriched watchlist item '{title}' from cache")
        except Exception as e:
            logger.warning(f"Background enrichment failed for '{title}': {e}")


@router.get("", response_model=List[watchlist_schema.WatchlistItem])
async def get_watchlist(
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db)
):
    result = await db.execute(select(models.WatchlistItem).filter(models.WatchlistItem.userId == current_user.id).order_by(models.WatchlistItem.updatedAt.desc()))
    return result.scalars().all()

@router.post("", response_model=watchlist_schema.WatchlistItem)
async def create_watchlist_item(
    item_in: watchlist_schema.WatchlistItemCreate,
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db)
):
    # check for uniqueness
    res = await db.execute(select(models.WatchlistItem).filter(
        models.WatchlistItem.userId == current_user.id,
        models.WatchlistItem.title == item_in.title,
        models.WatchlistItem.mediaType == item_in.mediaType
    ))
    existing = res.scalars().first()
    if existing:
        raise HTTPException(status_code=400, detail="Item already exists in your watchlist")

    # Check the global cache for pre-existing metadata
    cached = await get_cached(db, item_in.title, item_in.mediaType, item_in.year)
    item_data = item_in.model_dump()

    # Auto-populate from cache if available
    if cached:
        if not item_data.get("coverUrl") and cached.coverUrl:
            item_data["coverUrl"] = cached.coverUrl
        if (not item_data.get("genres") or len(item_data["genres"]) == 0) and cached.genres:
            item_data["genres"] = cached.genres
        if item_data.get("runtime") is None and cached.runtime is not None:
            item_data["runtime"] = cached.runtime
        if not item_data.get("description") and cached.description:
            item_data["description"] = cached.description

    db_item = models.WatchlistItem(
        id=str(uuid.uuid4()),
        userId=current_user.id,
        **item_data
    )
    db.add(db_item)
    await db.commit()
    await db.refresh(db_item)

    # Trigger background enrichment (fetches missing poster/runtime from external APIs)
    background_tasks.add_task(
        _background_enrich, item_in.title, item_in.mediaType, item_in.year, db_item.id
    )

    return db_item

@router.patch("/{item_id}", response_model=watchlist_schema.WatchlistItem)
async def update_watchlist_item(
    item_id: str,
    item_in: watchlist_schema.WatchlistItemUpdate,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db)
):
    result = await db.execute(select(models.WatchlistItem).filter(models.WatchlistItem.id == item_id, models.WatchlistItem.userId == current_user.id))
    db_item = result.scalars().first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Watchlist item not found")

    update_data = item_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_item, field, value)

    await db.commit()
    await db.refresh(db_item)
    return db_item

@router.delete("/{item_id}")
async def delete_watchlist_item(
    item_id: str,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db)
):
    result = await db.execute(select(models.WatchlistItem).filter(models.WatchlistItem.id == item_id, models.WatchlistItem.userId == current_user.id))
    db_item = result.scalars().first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Watchlist item not found")

    await db.delete(db_item)
    await db.commit()
    return {"ok": True}
