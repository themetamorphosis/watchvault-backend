"""MediaCache expiry.

schema.md describes `updatedAt` as the "Basis for TTL expiry", but nothing read
it that way: `get_cached` matched on key alone, so a poster URL TMDB rotated or
a runtime for a still-airing series was cached permanently with no invalidation
path short of manual SQL.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import models
from app.services import media_service
from app.services.media_service import (
    CACHE_TTL_DAYS,
    RUNNING_SERIES_TTL_DAYS,
    get_cached,
    is_cache_entry_fresh,
    upsert_cache,
)


def _aged(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


async def _make_entry(db, *, title, media_type, year, age_days, **fields):
    entry = models.MediaCache(
        id=f"cache-{title}-{media_type}",
        title=title,
        mediaType=media_type,
        year=year,
        **fields,
    )
    db.add(entry)
    await db.commit()

    # server_default=now() wins on insert, so age the row afterwards.
    entry.updatedAt = _aged(age_days)
    await db.commit()
    await db.refresh(entry)
    return entry


@pytest.mark.asyncio
async def test_fresh_entry_is_returned(db):
    await _make_entry(
        db, title="Dune", media_type="movie", year=2021, age_days=1,
        coverUrl="https://image.tmdb.org/x.jpg",
    )
    assert await get_cached(db, "Dune", "movie", 2021) is not None


@pytest.mark.asyncio
async def test_expired_entry_is_treated_as_a_miss(db):
    await _make_entry(
        db, title="Arrival", media_type="movie", year=2016,
        age_days=CACHE_TTL_DAYS + 1, coverUrl="https://image.tmdb.org/stale.jpg",
    )
    assert await get_cached(db, "Arrival", "movie", 2016) is None


@pytest.mark.asyncio
async def test_expired_entry_row_is_not_deleted(db):
    """A miss must not destroy the row — upsert_cache still needs to find it."""
    await _make_entry(
        db, title="Alien", media_type="movie", year=1979,
        age_days=CACHE_TTL_DAYS + 1, coverUrl="https://image.tmdb.org/old.jpg",
    )
    assert await get_cached(db, "Alien", "movie", 1979) is None

    rows = (
        await db.execute(
            select(models.MediaCache).filter(models.MediaCache.title == "Alien")
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_tv_entries_expire_on_the_shorter_window(db):
    """A still-airing series gains episodes, so its total runtime goes stale fast."""
    age = RUNNING_SERIES_TTL_DAYS + 1
    assert age < CACHE_TTL_DAYS, "the short window must actually be shorter"

    await _make_entry(
        db, title="Severance", media_type="tv", year=2022, age_days=age, runtime=500,
    )
    assert await get_cached(db, "Severance", "tv", 2022) is None


@pytest.mark.asyncio
async def test_movie_entries_keep_the_long_window(db):
    """A released film's runtime does not change; don't re-fetch it weekly."""
    await _make_entry(
        db, title="Heat", media_type="movie", year=1995,
        age_days=RUNNING_SERIES_TTL_DAYS + 1, runtime=170,
    )
    assert await get_cached(db, "Heat", "movie", 1995) is not None


@pytest.mark.asyncio
async def test_upsert_refreshes_an_expired_entry_in_place(db):
    """Re-fetching must update the existing row, not violate the unique key."""
    await _make_entry(
        db, title="Blade Runner", media_type="movie", year=1982,
        age_days=CACHE_TTL_DAYS + 5, coverUrl="https://image.tmdb.org/stale.jpg",
    )

    await upsert_cache(
        db, "Blade Runner", "movie", 1982,
        cover_url="https://image.tmdb.org/fresh.jpg",
    )

    rows = (
        await db.execute(
            select(models.MediaCache).filter(models.MediaCache.title == "Blade Runner")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].coverUrl == "https://image.tmdb.org/fresh.jpg"
    assert await get_cached(db, "Blade Runner", "movie", 1982) is not None


@pytest.mark.asyncio
async def test_upsert_still_fills_gaps_without_overwriting_fresh_values(db):
    """Existing fill-the-blanks behaviour must survive the TTL change."""
    await _make_entry(
        db, title="Whiplash", media_type="movie", year=2014, age_days=0,
        coverUrl="https://image.tmdb.org/keep.jpg",
    )

    await upsert_cache(
        db, "Whiplash", "movie", 2014,
        cover_url="https://image.tmdb.org/other.jpg", runtime=107,
    )

    entry = await get_cached(db, "Whiplash", "movie", 2014)
    assert entry.coverUrl == "https://image.tmdb.org/keep.jpg"  # not overwritten
    assert entry.runtime == 107  # gap filled


def test_freshness_helper_handles_a_naive_timestamp():
    """SQLite and some drivers hand back naive datetimes; don't crash on them."""
    naive = datetime.utcnow() - timedelta(days=1)
    assert is_cache_entry_fresh(naive, "movie") is True


def test_freshness_helper_treats_missing_timestamp_as_stale():
    assert is_cache_entry_fresh(None, "movie") is False


@pytest.mark.asyncio
async def test_expired_poster_lookup_refetches(client, db, monkeypatch):
    """End to end: a stale cache entry must not short-circuit the endpoint."""
    await _make_entry(
        db, title="Tenet", media_type="movie", year=2020,
        age_days=CACHE_TTL_DAYS + 1, coverUrl="https://image.tmdb.org/stale.jpg",
    )

    called = {}

    async def fake_fetch(db_, title, media_type, year):
        called["hit"] = True
        return {
            "coverUrl": "https://image.tmdb.org/fresh.jpg",
            "genres": ["Action"],
            "description": "new",
        }

    monkeypatch.setattr(
        media_service, "fetch_and_cache_poster", fake_fetch
    )
    import app.api.endpoints.media_search as ms

    monkeypatch.setattr(ms, "fetch_and_cache_poster", fake_fetch)

    res = await client.get(
        "/api/v1/media/poster", params={"title": "Tenet", "type": "movie", "year": 2020}
    )
    assert res.status_code == 200
    assert called.get("hit") is True
    assert res.json()["coverUrl"] == "https://image.tmdb.org/fresh.jpg"
