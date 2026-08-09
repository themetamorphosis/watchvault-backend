from typing import List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.api import dependencies
from app.db import models
from app.schemas import watchlist as watchlist_schema
from app.api.endpoints.media import get_cached, enrich_media_cache
from app.services.media_service import get_cached_bulk
from app.db.database import AsyncSessionLocal
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
                if not item:
                    return  # Item deleted before enrichment completed
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
        except Exception:
            await db.rollback()
            logger.exception(f"Background enrichment failed for '{title}'")


@router.get("", response_model=watchlist_schema.WatchlistPage)
async def get_watchlist(
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
    # Defaults to a page rather than the whole library. `0` still means "all"
    # for callers that ask for it explicitly. Flipping this default is safe
    # only because the frontend now always sends an explicit limit and follows
    # `has_more` — see REMEDIATION_PLAN.md 2.6/2.10 for the deploy ordering.
    limit: int = Query(default=100, ge=0, le=500, description="Max items to return. 0 = all"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
):
    # Get total count
    count_stmt = select(func.count()).select_from(models.WatchlistItem).where(
        models.WatchlistItem.userId == current_user.id
    )
    total = (await db.execute(count_stmt)).scalar()

    # Get page
    q = (
        select(models.WatchlistItem)
        .filter(models.WatchlistItem.userId == current_user.id)
        .order_by(models.WatchlistItem.updatedAt.desc())
    )
    if offset > 0:
        q = q.offset(offset)
    if limit > 0:
        q = q.limit(limit)
    result = await db.execute(q)
    items = result.scalars().all()

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        # limit=0 means "return everything", so nothing can remain after it.
        "has_more": (offset + limit) < total if limit > 0 else False,
    }

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
    try:
        await db.commit()
    except IntegrityError:
        # Two concurrent requests can both pass the SELECT above and race to
        # INSERT. The unique constraint is the real arbiter; surface it as the
        # documented 400 rather than letting it bubble up as a 500.
        await db.rollback()
        raise HTTPException(status_code=400, detail="Item already exists in your watchlist")
    await db.refresh(db_item)

    # Trigger background enrichment (fetches missing poster/runtime from external APIs)
    background_tasks.add_task(
        _background_enrich, item_in.title, item_in.mediaType, item_in.year, db_item.id
    )

    return db_item



MAX_BATCH_SIZE = 100


@router.post("/batch", response_model=watchlist_schema.BatchImportResult)
async def batch_import_items(
    items: List[watchlist_schema.WatchlistItemCreate],
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db)
):
    """Import multiple items at once. Skips duplicates silently."""
    if len(items) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Batch size {len(items)} exceeds maximum of {MAX_BATCH_SIZE}"
        )
    skipped = 0

    # Existing titles for this user, projected rather than hydrated: only the
    # title and type are needed to build the duplicate set.
    result = await db.execute(
        select(
            func.lower(models.WatchlistItem.title), models.WatchlistItem.mediaType
        ).filter(models.WatchlistItem.userId == current_user.id)
    )
    existing_set = {(title, media_type) for title, media_type in result.all()}

    # One cache lookup for the whole batch instead of one per item.
    cache_by_key = await get_cached_bulk(
        db, [(i.title, i.mediaType, i.year) for i in items]
    )

    rows: list[dict] = []
    pending: dict[str, tuple[str, str, int | None]] = {}

    for item_in in items:
        # Case-insensitive pre-filter for a friendlier `skipped` count. The
        # database constraint is case-sensitive, so ON CONFLICT below remains
        # the actual arbiter.
        key = (item_in.title.lower(), item_in.mediaType)
        if key in existing_set:
            skipped += 1
            continue

        cached = cache_by_key.get((item_in.title, item_in.mediaType, item_in.year))
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

        item_id = str(uuid.uuid4())
        rows.append({"id": item_id, "userId": current_user.id, **item_data})
        pending[item_id] = (item_in.title, item_in.mediaType, item_in.year)
        existing_set.add(key)  # Prevent duplicates within the batch

    imported = 0
    inserted_ids: list[str] = []

    if rows:
        # ON CONFLICT DO NOTHING: a title that collides with an existing row
        # (including one differing only by case) is skipped instead of aborting
        # the whole batch with an IntegrityError.
        stmt = (
            pg_insert(models.WatchlistItem)
            .values(rows)
            .on_conflict_do_nothing(
                constraint="watchlistitem_userid_title_mediatype_key"
            )
            .returning(models.WatchlistItem.id)
        )
        inserted_ids = list((await db.execute(stmt)).scalars().all())
        await db.commit()
        imported = len(inserted_ids)
        skipped += len(rows) - imported

    # Queue enrichment only after the commit succeeds, and only for rows that
    # actually landed — previously tasks were queued for rows that a failed
    # commit never created.
    for item_id in inserted_ids:
        title, media_type, year = pending[item_id]
        background_tasks.add_task(_background_enrich, title, media_type, year, item_id)

    return {"success": True, "imported": imported, "skipped": skipped}


@router.patch(
    "/{item_id}/toggle-favorite",
    response_model=watchlist_schema.FavoriteToggleResult,
)
async def toggle_favorite(
    item_id: str,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    result = await db.execute(
        select(models.WatchlistItem).filter(
            models.WatchlistItem.id == item_id,
            models.WatchlistItem.userId == current_user.id,
        )
    )
    db_item = result.scalars().first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Watchlist item not found")

    db_item.favorite = not db_item.favorite
    await db.commit()
    await db.refresh(db_item)
    return {"id": db_item.id, "favorite": db_item.favorite}


# Mirrors WatchlistItemUpdate. Previously listed 12 fields that don't exist on
# the model at all (poster_path, rating, tags, priority, watched_at, …), which
# implied a schema that was never real.
ALLOWED_UPDATE_FIELDS = {
    "status", "favorite", "genres", "notes", "description",
    "year", "endYear", "running", "coverUrl", "runtime",
}

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
        if field in ALLOWED_UPDATE_FIELDS:
            setattr(db_item, field, value)

    await db.commit()
    await db.refresh(db_item)
    return db_item

@router.delete("/{item_id}", response_model=watchlist_schema.DeleteResult)
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
