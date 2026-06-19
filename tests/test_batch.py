import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_batch_import_creates_items(client: AsyncClient, auth_headers):
    items = [
        {"title": f"Batch Movie {i}", "mediaType": "movie", "status": "pending"}
        for i in range(3)
    ]
    res = await client.post("/api/v1/watchlist/batch", json=items, headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["imported"] == 3
    assert data["skipped"] == 0


@pytest.mark.asyncio
async def test_batch_import_rejects_over_100(client: AsyncClient, auth_headers):
    items = [
        {"title": f"Overflow {i}", "mediaType": "movie", "status": "pending"}
        for i in range(101)
    ]
    res = await client.post("/api/v1/watchlist/batch", json=items, headers=auth_headers)
    assert res.status_code == 413
    assert "exceeds maximum" in res.json()["detail"]


@pytest.mark.asyncio
async def test_batch_import_skips_duplicates(client: AsyncClient, auth_headers):
    title = f"Dupe {uuid.uuid4().hex[:6]}"
    items = [
        {"title": title, "mediaType": "movie", "status": "pending"},
        {"title": title, "mediaType": "movie", "status": "watched"},
    ]
    res = await client.post("/api/v1/watchlist/batch", json=items, headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["imported"] == 1
    assert data["skipped"] == 1


@pytest.mark.asyncio
async def test_batch_import_skips_existing_items(client: AsyncClient, auth_headers):
    title = f"Existing {uuid.uuid4().hex[:6]}"
    # Create first
    await client.post("/api/v1/watchlist", json={
        "title": title, "mediaType": "movie", "status": "pending"
    }, headers=auth_headers)

    # Try to batch import the same title
    items = [
        {"title": title, "mediaType": "movie", "status": "pending"},
        {"title": f"New {uuid.uuid4().hex[:6]}", "mediaType": "movie", "status": "pending"},
    ]
    res = await client.post("/api/v1/watchlist/batch", json=items, headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["imported"] == 1
    assert data["skipped"] == 1


@pytest.mark.asyncio
async def test_batch_import_no_auth(client: AsyncClient):
    items = [{"title": "No Auth", "mediaType": "movie", "status": "pending"}]
    res = await client.post("/api/v1/watchlist/batch", json=items)
    assert res.status_code == 401
