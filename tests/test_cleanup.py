"""Tests for the startup cleanup service."""
import pytest
import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import models
from app.services.cleanup import run_cleanup


@pytest.mark.asyncio
async def test_cleanup_fixes_null_updated_at(db: AsyncSession, test_user: models.User):
    """Verify that rows with null updatedAt get repaired."""
    item_id = str(uuid.uuid4())
    await db.execute(text(
        '''INSERT INTO "WatchlistItem" 
        ("id", "userId", "title", "mediaType", "status", "favorite", "genres", "createdAt", "updatedAt")
        VALUES (:item_id, :uid, 'Null TS Movie', 'movie', 'watched', false, '{}', NOW(), NULL)'''
    ), {"item_id": item_id, "uid": test_user.id})
    await db.commit()

    await run_cleanup(db)

    result = await db.execute(text(
        '''SELECT "updatedAt" FROM "WatchlistItem" WHERE "id" = :item_id'''
    ), {"item_id": item_id})
    row = result.fetchone()
    assert row is not None
    assert row[0] is not None, "updatedAt should have been repaired by cleanup"


@pytest.mark.asyncio
async def test_cleanup_fixes_null_genres(db: AsyncSession, test_user: models.User):
    """Verify that rows with null genres get repaired."""
    item_id = str(uuid.uuid4())
    await db.execute(text(
        '''INSERT INTO "WatchlistItem" 
        ("id", "userId", "title", "mediaType", "status", "favorite", "createdAt", "updatedAt")
        VALUES (:item_id, :uid, 'Null Genres Movie', 'movie', 'watched', false, NOW(), NOW())'''
    ), {"item_id": item_id, "uid": test_user.id})
    await db.commit()

    await run_cleanup(db)

    result = await db.execute(text(
        '''SELECT "genres" FROM "WatchlistItem" WHERE "id" = :item_id'''
    ), {"item_id": item_id})
    row = result.fetchone()
    assert row is not None
    assert row[0] is not None, "genres should have been set to empty array"


@pytest.mark.asyncio
async def test_cleanup_is_idempotent(db: AsyncSession):
    """Running cleanup twice should not break anything."""
    await run_cleanup(db)
    await run_cleanup(db)
