"""Pagination, cache hydration, and the batch upsert path."""

import uuid

import pytest

from app.db import models


def _item(title: str, media_type: str = "movie", status: str = "pending", **over):
    payload = {"title": title, "mediaType": media_type, "status": status}
    payload.update(over)
    return payload


async def _seed(db, user, count: int):
    for i in range(count):
        db.add(models.WatchlistItem(
            id=str(uuid.uuid4()),
            userId=user.id,
            title=f"Title {i:02d}",
            mediaType="movie",
            status="watched",
        ))
    await db.commit()


# ═══════════════════════════════════════════════════════════════
#  Pagination
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pagination_limit_and_offset(client, auth_headers, db, test_user):
    await _seed(db, test_user, 10)

    res = await client.get("/api/v1/watchlist?limit=4&offset=0", headers=auth_headers)
    body = res.json()
    assert body["total"] == 10
    assert len(body["items"]) == 4
    assert body["has_more"] is True

    res2 = await client.get("/api/v1/watchlist?limit=4&offset=8", headers=auth_headers)
    body2 = res2.json()
    assert len(body2["items"]) == 2
    assert body2["has_more"] is False


@pytest.mark.asyncio
async def test_limit_zero_returns_everything(client, auth_headers, db, test_user):
    await _seed(db, test_user, 5)
    body = (await client.get("/api/v1/watchlist", headers=auth_headers)).json()
    assert body["total"] == 5
    assert len(body["items"]) == 5
    assert body["has_more"] is False


@pytest.mark.asyncio
async def test_limit_above_maximum_rejected(client, auth_headers):
    res = await client.get("/api/v1/watchlist?limit=501", headers=auth_headers)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_watchlist_is_scoped_to_the_owner(client, auth_headers, db, test_user):
    other = models.User(id=str(uuid.uuid4()), email="other@example.com", password="x")
    db.add(other)
    db.add(models.WatchlistItem(
        id=str(uuid.uuid4()), userId=other.id,
        title="Not Yours", mediaType="movie", status="watched",
    ))
    await db.commit()

    body = (await client.get("/api/v1/watchlist", headers=auth_headers)).json()
    assert all(i["title"] != "Not Yours" for i in body["items"])


# ═══════════════════════════════════════════════════════════════
#  MediaCache hydration on create
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_hydrates_missing_fields_from_media_cache(
    client, auth_headers, db
):
    db.add(models.MediaCache(
        id=str(uuid.uuid4()),
        title="Cached Movie",
        mediaType="movie",
        year=2001,
        coverUrl="http://img/cached.jpg",
        genres=["Drama"],
        description="From cache.",
        runtime=120,
    ))
    await db.commit()

    res = await client.post(
        "/api/v1/watchlist",
        json=_item("Cached Movie", year=2001),
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["coverUrl"] == "http://img/cached.jpg"
    assert body["genres"] == ["Drama"]
    assert body["runtime"] == 120


@pytest.mark.asyncio
async def test_create_does_not_overwrite_supplied_values(client, auth_headers, db):
    db.add(models.MediaCache(
        id=str(uuid.uuid4()), title="Override Me", mediaType="movie", year=2001,
        coverUrl="http://img/cached.jpg", genres=["Drama"],
    ))
    await db.commit()

    res = await client.post(
        "/api/v1/watchlist",
        json=_item("Override Me", year=2001, coverUrl="http://img/mine.jpg"),
        headers=auth_headers,
    )
    assert res.json()["coverUrl"] == "http://img/mine.jpg"


# ═══════════════════════════════════════════════════════════════
#  Batch import
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_batch_imports_all_new_items(client, auth_headers):
    res = await client.post(
        "/api/v1/watchlist/batch",
        json=[_item("Batch A"), _item("Batch B"), _item("Batch C")],
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json() == {"success": True, "imported": 3, "skipped": 0}


@pytest.mark.asyncio
async def test_batch_skips_case_insensitive_duplicates(client, auth_headers):
    await client.post(
        "/api/v1/watchlist", json=_item("Dune", year=2021), headers=auth_headers
    )
    res = await client.post(
        "/api/v1/watchlist/batch",
        json=[_item("dune", year=2021), _item("Brand New")],
        headers=auth_headers,
    )
    body = res.json()
    assert body["imported"] == 1
    assert body["skipped"] == 1


@pytest.mark.asyncio
async def test_batch_with_internal_duplicates_does_not_error(client, auth_headers):
    """Duplicates inside one payload must not abort the batch with a 500."""
    res = await client.post(
        "/api/v1/watchlist/batch",
        json=[_item("Same Title"), _item("Same Title"), _item("Other")],
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["imported"] == 2
    assert body["skipped"] == 1


@pytest.mark.asyncio
async def test_batch_over_maximum_rejected(client, auth_headers):
    payload = [_item(f"Bulk {i}") for i in range(101)]
    res = await client.post(
        "/api/v1/watchlist/batch", json=payload, headers=auth_headers
    )
    assert res.status_code == 413


@pytest.mark.asyncio
async def test_batch_requires_auth(client):
    res = await client.post("/api/v1/watchlist/batch", json=[_item("X")])
    assert res.status_code == 401


# ═══════════════════════════════════════════════════════════════
#  Update field allowlist
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_update_applies_known_fields(client, auth_headers):
    created = (await client.post(
        "/api/v1/watchlist", json=_item("Updatable"), headers=auth_headers
    )).json()

    res = await client.patch(
        f"/api/v1/watchlist/{created['id']}",
        json={"status": "watched", "notes": "Done.", "runtime": 99},
        headers=auth_headers,
    )
    body = res.json()
    assert body["status"] == "watched"
    assert body["notes"] == "Done."
    assert body["runtime"] == 99


@pytest.mark.asyncio
async def test_update_rejects_invalid_status(client, auth_headers):
    created = (await client.post(
        "/api/v1/watchlist", json=_item("Bad Status"), headers=auth_headers
    )).json()

    res = await client.patch(
        f"/api/v1/watchlist/{created['id']}",
        json={"status": "not-a-status"},
        headers=auth_headers,
    )
    assert res.status_code == 422
