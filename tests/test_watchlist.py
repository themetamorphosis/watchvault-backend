import uuid
import pytest
from httpx import AsyncClient


async def _create_item(client: AsyncClient, auth_headers: dict, **overrides) -> dict:
    payload = {
        "title": f"Test Movie {uuid.uuid4().hex[:6]}",
        "mediaType": "movie",
        "status": "pending",
        "year": 2024,
        **overrides,
    }
    res = await client.post("/api/v1/watchlist", json=payload, headers=auth_headers)
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.asyncio
async def test_create_item(client: AsyncClient, auth_headers):
    data = await _create_item(client, auth_headers)
    assert "id" in data
    assert data["mediaType"] == "movie"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_create_item_missing_title(client: AsyncClient, auth_headers):
    res = await client.post("/api/v1/watchlist", json={
        "mediaType": "movie",
        "status": "pending",
    }, headers=auth_headers)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_create_item_invalid_media_type(client: AsyncClient, auth_headers):
    res = await client.post("/api/v1/watchlist", json={
        "title": "Bad Type",
        "mediaType": "book",
        "status": "pending",
    }, headers=auth_headers)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_create_item_invalid_status(client: AsyncClient, auth_headers):
    res = await client.post("/api/v1/watchlist", json={
        "title": "Bad Status",
        "mediaType": "movie",
        "status": "invalid_status",
    }, headers=auth_headers)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_create_item_no_auth(client: AsyncClient):
    res = await client.post("/api/v1/watchlist", json={
        "title": "No Auth",
        "mediaType": "movie",
        "status": "pending",
    })
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_list_items(client: AsyncClient, auth_headers):
    await _create_item(client, auth_headers, title="Movie A")
    await _create_item(client, auth_headers, title="Movie B")

    res = await client.get("/api/v1/watchlist", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    items = data["items"] if isinstance(data, dict) else data
    assert len(items) >= 2
    assert data["total"] >= 2 if isinstance(data, dict) else True


@pytest.mark.asyncio
async def test_list_items_only_own(client: AsyncClient, auth_headers, db, test_user):
    # Create item for test_user
    await _create_item(client, auth_headers, title="My Movie")

    # Create another user with their own item
    from app.db import models
    from app.core import security
    other = models.User(
        id=str(uuid.uuid4()),
        name="Other",
        email=f"other-{uuid.uuid4().hex[:6]}@example.com",
        password=security.get_password_hash("Pass123!"),
    )
    db.add(other)
    await db.commit()

    other_item = models.WatchlistItem(
        id=str(uuid.uuid4()),
        userId=other.id,
        title="Other Movie",
        mediaType="movie",
        status="pending",
    )
    db.add(other_item)
    await db.commit()

    res = await client.get("/api/v1/watchlist", headers=auth_headers)
    data = res.json()
    items = data["items"] if isinstance(data, dict) else data
    titles = [i["title"] for i in items]
    assert "My Movie" in titles
    assert "Other Movie" not in titles


@pytest.mark.asyncio
async def test_update_item(client: AsyncClient, auth_headers):
    item = await _create_item(client, auth_headers, title="Updatable")

    res = await client.patch(f"/api/v1/watchlist/{item['id']}", json={
        "status": "watched",
        "favorite": True,
    }, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["status"] == "watched"
    assert res.json()["favorite"] is True


@pytest.mark.asyncio
async def test_update_item_not_found(client: AsyncClient, auth_headers):
    res = await client.patch("/api/v1/watchlist/nonexistent-id", json={
        "status": "watched",
    }, headers=auth_headers)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_delete_item(client: AsyncClient, auth_headers):
    item = await _create_item(client, auth_headers, title="Deletable")

    res = await client.delete(f"/api/v1/watchlist/{item['id']}", headers=auth_headers)
    assert res.status_code == 200

    # Verify it's gone
    res = await client.get("/api/v1/watchlist", headers=auth_headers)
    data = res.json()
    items = data["items"] if isinstance(data, dict) else data
    ids = [i["id"] for i in items]
    assert item["id"] not in ids


@pytest.mark.asyncio
async def test_delete_item_not_found(client: AsyncClient, auth_headers):
    res = await client.delete("/api/v1/watchlist/nonexistent-id", headers=auth_headers)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_create_duplicate_item(client: AsyncClient, auth_headers):
    title = f"Duplicate {uuid.uuid4().hex[:6]}"
    await _create_item(client, auth_headers, title=title, mediaType="movie")
    res = await client.post("/api/v1/watchlist", json={
        "title": title,
        "mediaType": "movie",
        "status": "pending",
    }, headers=auth_headers)
    assert res.status_code == 400
