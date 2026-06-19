"""Startup cleanup — auto-repairs inconsistent DB rows.

Runs once at app startup via the lifespan context manager.
Every fix is idempotent and safe to run on every boot.
"""

import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def run_cleanup(db: AsyncSession) -> None:
    """Repair any rows that have inconsistent or missing data."""

    # 1. Fix null updatedAt on WatchlistItem
    result = await db.execute(text(
        'UPDATE "WatchlistItem" SET "updatedAt" = COALESCE("createdAt", NOW()) '
        'WHERE "updatedAt" IS NULL'
    ))
    if result.rowcount > 0:
        logger.info(f"[cleanup] Fixed {result.rowcount} WatchlistItem rows with null updatedAt")

    # 2. Fix null createdAt on WatchlistItem
    result = await db.execute(text(
        'UPDATE "WatchlistItem" SET "createdAt" = NOW() '
        'WHERE "createdAt" IS NULL'
    ))
    if result.rowcount > 0:
        logger.info(f"[cleanup] Fixed {result.rowcount} WatchlistItem rows with null createdAt")

    # 3. Fix null updatedAt on User
    result = await db.execute(text(
        'UPDATE "User" SET "updatedAt" = COALESCE("createdAt", NOW()) '
        'WHERE "updatedAt" IS NULL'
    ))
    if result.rowcount > 0:
        logger.info(f"[cleanup] Fixed {result.rowcount} User rows with null updatedAt")

    # 4. Fix null updatedAt on MediaCache
    result = await db.execute(text(
        'UPDATE "MediaCache" SET "updatedAt" = COALESCE("createdAt", NOW()) '
        'WHERE "updatedAt" IS NULL'
    ))
    if result.rowcount > 0:
        logger.info(f"[cleanup] Fixed {result.rowcount} MediaCache rows with null updatedAt")

    # 5. Fix empty genres arrays (null → empty list)
    result = await db.execute(text(
        'UPDATE "WatchlistItem" SET "genres" = \'{}\' '
        'WHERE "genres" IS NULL'
    ))
    if result.rowcount > 0:
        logger.info(f"[cleanup] Fixed {result.rowcount} WatchlistItem rows with null genres")

    # 6. Fix empty genres on MediaCache
    result = await db.execute(text(
        'UPDATE "MediaCache" SET "genres" = \'{}\' '
        'WHERE "genres" IS NULL'
    ))
    if result.rowcount > 0:
        logger.info(f"[cleanup] Fixed {result.rowcount} MediaCache rows with null genres")

    await db.commit()
    logger.info("[cleanup] Startup cleanup complete")
