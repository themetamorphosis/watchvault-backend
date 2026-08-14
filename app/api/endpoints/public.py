"""Unauthenticated read-only endpoints.

Everything in this module is served to anonymous visitors, so it lives in its
own file rather than beside the authenticated watchlist routes — the auth
boundary should be visible from the filename, not buried in a decorator.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import dependencies
from app.db import models
from app.middleware.rate_limit import limiter
from app.schemas import share as share_schema

router = APIRouter()


def _apply_filters(stmt, link: models.ShareLink):
    """Narrow a WatchlistItem query to what `link` is allowed to expose.

    An empty filter array means "everything on this dimension", so an empty
    list must not become `IN ()`.
    """
    if link.statuses:
        stmt = stmt.where(models.WatchlistItem.status.in_(link.statuses))
    if link.mediaTypes:
        stmt = stmt.where(models.WatchlistItem.mediaType.in_(link.mediaTypes))
    if link.favoritesOnly:
        stmt = stmt.where(models.WatchlistItem.favorite.is_(True))
    return stmt


@router.get("/watchlist/{slug}", response_model=share_schema.PublicWatchlist)
@limiter.limit("30/minute")
async def get_public_watchlist(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(dependencies.get_db),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Serve a shared watchlist to anyone holding the link — no login.

    The response is assembled from `PublicWatchlistItem`, which omits `notes`
    and `userId`; returning ORM rows directly here would leak both.
    """
    result = await db.execute(
        select(models.ShareLink, models.User)
        .join(models.User, models.User.id == models.ShareLink.userId)
        .filter(models.ShareLink.slug == share_schema.normalize_slug(slug))
    )
    row = result.first()
    if not row:
        # Revoked and never-existed are the same answer on purpose.
        raise HTTPException(status_code=404, detail="This share link is not available")
    link, owner = row

    base = select(models.WatchlistItem).filter(
        models.WatchlistItem.userId == link.userId
    )
    base = _apply_filters(base, link)

    total = (
        await db.execute(
            _apply_filters(
                select(func.count())
                .select_from(models.WatchlistItem)
                .where(models.WatchlistItem.userId == link.userId),
                link,
            )
        )
    ).scalar()

    items = (
        await db.execute(
            base.order_by(models.WatchlistItem.updatedAt.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    return {
        "slug": link.slug,
        "label": link.label,
        "owner": {"name": owner.name, "image": owner.image},
        "filters": {
            "statuses": link.statuses or [],
            "mediaTypes": link.mediaTypes or [],
            "favoritesOnly": bool(link.favoritesOnly),
        },
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + limit) < total,
    }
