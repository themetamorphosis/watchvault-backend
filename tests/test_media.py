import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_search_requires_query(client: AsyncClient):
    res = await client.get("/api/v1/media/search")
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_search_min_length(client: AsyncClient):
    res = await client.get("/api/v1/media/search", params={"q": "a", "type": "movie"})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_search_invalid_type(client: AsyncClient):
    res = await client.get("/api/v1/media/search", params={"q": "Batman", "type": "book"})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_poster_requires_title(client: AsyncClient):
    res = await client.get("/api/v1/media/poster")
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_runtime_requires_title(client: AsyncClient):
    res = await client.get("/api/v1/media/runtime")
    assert res.status_code == 422
