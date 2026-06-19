import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient


def _mock_tmdb_response(data: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    return resp


@pytest.mark.asyncio
@patch("app.api.endpoints.media_details.get_shared_client")
async def test_get_details_returns_tmdb_data(mock_get_client, client: AsyncClient):
    mock_client = AsyncMock()
    mock_get_client.return_value = mock_client

    mock_client.get.side_effect = [
        _mock_tmdb_response({
            "title": "The Matrix",
            "release_date": "1999-03-31",
            "overview": "A computer hacker...",
            "genres": [{"name": "Action"}, {"name": "Sci-Fi"}],
            "vote_average": 8.7,
            "vote_count": 25000,
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "runtime": 136,
        }),
        _mock_tmdb_response({
            "cast": [{"name": "Keanu Reeves", "character": "Neo", "profile_path": "/keanu.jpg"}],
            "crew": [
                {"name": "Wachowskis", "job": "Director"},
                {"name": "Don Davis", "job": "Original Music Composer"},
            ],
        }),
        _mock_tmdb_response({
            "results": [{"site": "YouTube", "type": "Trailer", "key": "abc123"}],
        }),
    ]

    res = await client.get("/api/v1/media/details?tmdb_id=603&type=movie")
    assert res.status_code == 200
    data = res.json()
    assert data["title"] == "The Matrix"
    assert data["year"] == 1999
    assert data["runtime"] == 136
    assert "Action" in data["genres"]
    assert data["trailerKey"] == "abc123"
    assert len(data["directors"]) > 0


@pytest.mark.asyncio
@patch("app.api.endpoints.media_details.get_shared_client")
async def test_get_details_returns_404_for_invalid(mock_get_client, client: AsyncClient):
    mock_client = AsyncMock()
    mock_get_client.return_value = mock_client
    mock_client.get.return_value = _mock_tmdb_response({}, status=404)

    res = await client.get("/api/v1/media/details?tmdb_id=999999&type=movie")
    assert res.status_code == 404


@pytest.mark.asyncio
@patch("app.api.endpoints.media_details.get_shared_client")
async def test_get_details_returns_502_on_tmdb_error(mock_get_client, client: AsyncClient):
    mock_client = AsyncMock()
    mock_get_client.return_value = mock_client
    mock_client.get.return_value = _mock_tmdb_response({}, status=500)

    res = await client.get("/api/v1/media/details?tmdb_id=603&type=movie")
    assert res.status_code == 502


@pytest.mark.asyncio
async def test_get_details_requires_tmdb_id(client: AsyncClient):
    res = await client.get("/api/v1/media/details?type=movie")
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_get_details_invalid_type(client: AsyncClient):
    res = await client.get("/api/v1/media/details?tmdb_id=603&type=book")
    assert res.status_code == 422
