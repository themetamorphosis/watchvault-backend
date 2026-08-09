"""Batch import must not scale its query count with the batch size.

The loop previously called get_cached once per item, so a 100-item import cost
101 SELECTs where 2 suffice.
"""

import pytest
from sqlalchemy import event

from app.db import models
from app.services.media_service import get_cached_bulk


@pytest.fixture
def count_selects(db):
    """Count SELECT statements issued on the test session's connection."""
    counter = {"n": 0}
    # get_bind() already returns the sync Engine underlying the AsyncSession.
    sync_engine = db.get_bind()

    def before_cursor_execute(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    event.listen(sync_engine, "before_cursor_execute", before_cursor_execute)
    yield counter
    event.remove(sync_engine, "before_cursor_execute", before_cursor_execute)


def _item(title, media_type="movie", year=2020):
    return {
        "title": title,
        "mediaType": media_type,
        "status": "watched",
        "year": year,
    }


@pytest.mark.asyncio
async def test_batch_query_count_is_flat_in_batch_size(
    client, auth_headers, count_selects
):
    items = [_item(f"Film {i}", year=2000 + i) for i in range(40)]

    count_selects["n"] = 0
    res = await client.post(
        "/api/v1/watchlist/batch", json=items, headers=auth_headers
    )
    assert res.status_code == 200
    assert res.json()["imported"] == 40

    # Two lookups (existing titles + bulk cache) plus the insert's RETURNING and
    # the auth user fetch. Generous ceiling — the point is that it does not grow
    # with len(items), which would put this near 41.
    assert count_selects["n"] <= 8, (
        f"{count_selects['n']} SELECTs for 40 items — the per-item cache lookup is back"
    )


@pytest.mark.asyncio
async def test_bulk_cache_lookup_returns_only_matching_keys(db):
    db.add_all(
        [
            models.MediaCache(
                id="c1", title="Dune", mediaType="movie", year=2021,
                coverUrl="https://image.tmdb.org/dune.jpg",
            ),
            models.MediaCache(
                id="c2", title="Dune", mediaType="movie", year=1984,
                coverUrl="https://image.tmdb.org/dune84.jpg",
            ),
            models.MediaCache(
                id="c3", title="Dune", mediaType="tv", year=2021,
                coverUrl="https://image.tmdb.org/dunetv.jpg",
            ),
        ]
    )
    await db.commit()

    found = await get_cached_bulk(db, [("Dune", "movie", 2021), ("Dune", "tv", 2021)])

    assert set(found.keys()) == {("Dune", "movie", 2021), ("Dune", "tv", 2021)}
    assert found[("Dune", "movie", 2021)].coverUrl.endswith("dune.jpg")
    assert found[("Dune", "tv", 2021)].coverUrl.endswith("dunetv.jpg")


@pytest.mark.asyncio
async def test_bulk_cache_lookup_distinguishes_null_year(db):
    """(title, type, NULL) and (title, type, 2021) are distinct keys — schema.md."""
    db.add_all(
        [
            models.MediaCache(
                id="n1", title="Solaris", mediaType="movie", year=None, runtime=100
            ),
            models.MediaCache(
                id="n2", title="Solaris", mediaType="movie", year=1972, runtime=167
            ),
        ]
    )
    await db.commit()

    found = await get_cached_bulk(
        db, [("Solaris", "movie", None), ("Solaris", "movie", 1972)]
    )
    assert found[("Solaris", "movie", None)].runtime == 100
    assert found[("Solaris", "movie", 1972)].runtime == 167


@pytest.mark.asyncio
async def test_bulk_cache_lookup_is_empty_for_no_keys(db):
    assert await get_cached_bulk(db, []) == {}


@pytest.mark.asyncio
async def test_batch_still_hydrates_from_cache(client, auth_headers, db):
    """De-N+1'ing must not lose the metadata backfill it was doing."""
    db.add(
        models.MediaCache(
            id="hydrate-1",
            title="Cached Film",
            mediaType="movie",
            year=1999,
            coverUrl="https://image.tmdb.org/cached.jpg",
            genres=["Drama"],
            runtime=120,
            description="From the cache",
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/watchlist/batch",
        json=[_item("Cached Film", year=1999)],
        headers=auth_headers,
    )
    assert res.status_code == 200

    listed = await client.get("/api/v1/watchlist", headers=auth_headers)
    item = next(i for i in listed.json()["items"] if i["title"] == "Cached Film")
    assert item["coverUrl"] == "https://image.tmdb.org/cached.jpg"
    assert item["genres"] == ["Drama"]
    assert item["runtime"] == 120
    assert item["description"] == "From the cache"
