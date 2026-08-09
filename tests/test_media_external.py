"""External-API behaviour: TMDB / TVMaze / Jikan.

These paths were effectively untested — media_search.py sat at 13% coverage —
which is why bugs like unencoded titles in the query string survived. Every
request here is intercepted by respx; nothing touches the network.
"""

import httpx
import pytest
import respx

from app.services import media_service
from app.services.media_service import (
    cache_get,
    cache_set,
    fetch_anime_runtime,
    fetch_jikan_poster,
    fetch_movie_runtime,
    fetch_tmdb_movie_poster,
    fetch_tv_runtime,
    fetch_tvmaze_poster,
    parse_tmdb_results,
    TMDB_GENRES,
)

TMDB = "https://api.themoviedb.org/3"
TVMAZE = "https://api.tvmaze.com"
JIKAN = "https://api.jikan.moe/v4"


@pytest.fixture(autouse=True)
def _clear_search_cache():
    """The module-level search cache is process-global and would leak across tests."""
    media_service._search_cache.clear()
    yield
    media_service._search_cache.clear()


def _movie_result(**over):
    base = {
        "id": 603,
        "title": "The Matrix",
        "release_date": "1999-03-30",
        "poster_path": "/poster.jpg",
        "backdrop_path": "/backdrop.jpg",
        "overview": "A hacker learns the truth.",
        "genre_ids": [28, 878],
        "vote_average": 8.2,
    }
    base.update(over)
    return base


def _tv_result(**over):
    base = {
        "id": 1396,
        "name": "Breaking Bad",
        "first_air_date": "2008-01-20",
        "poster_path": "/bb.jpg",
        "backdrop_path": "/bb-back.jpg",
        "overview": "A teacher turns to crime.",
        "genre_ids": [18, 80],
        "vote_average": 8.9,
    }
    base.update(over)
    return base


# ═══════════════════════════════════════════════════════════════
#  In-memory search cache
# ═══════════════════════════════════════════════════════════════

def test_cache_roundtrip_and_miss():
    assert cache_get("absent") is None
    cache_set("k", [1, 2, 3])
    assert cache_get("k") == [1, 2, 3]


def test_cache_expires_after_ttl(monkeypatch):
    cache_set("k", ["fresh"])
    real = media_service._time.time()
    monkeypatch.setattr(media_service._time, "time", lambda: real + media_service._CACHE_TTL + 1)
    assert cache_get("k") is None


def test_cache_evicts_when_full():
    for i in range(media_service._CACHE_MAX + 10):
        cache_set(f"key-{i}", [i])
    assert len(media_service._search_cache) <= media_service._CACHE_MAX


# ═══════════════════════════════════════════════════════════════
#  TMDB result parsing
# ═══════════════════════════════════════════════════════════════

def test_parse_tmdb_results_maps_fields():
    parsed = parse_tmdb_results([_movie_result()], "movie", TMDB_GENRES)
    assert len(parsed) == 1
    item = parsed[0]
    assert item.tmdbId == 603
    assert item.title == "The Matrix"
    assert item.year == 1999
    assert item.posterUrl.endswith("/poster.jpg")
    assert "Action" in item.genres


def test_parse_tmdb_results_handles_missing_optional_fields():
    parsed = parse_tmdb_results(
        [{"id": 1, "title": "No Meta", "release_date": "", "genre_ids": []}],
        "movie",
        TMDB_GENRES,
    )
    assert parsed[0].year is None
    assert parsed[0].posterUrl is None
    assert parsed[0].genres == []


# ═══════════════════════════════════════════════════════════════
#  Fetchers — TVMaze
# ═══════════════════════════════════════════════════════════════

@respx.mock
@pytest.mark.asyncio
async def test_tvmaze_poster_strips_html_from_summary():
    respx.get(f"{TVMAZE}/search/shows").mock(
        return_value=httpx.Response(
            200,
            json=[{
                "show": {
                    "image": {"original": "http://img/orig.jpg", "medium": "http://img/med.jpg"},
                    "genres": ["Drama"],
                    "summary": "<p>A <b>great</b> show.</p>",
                }
            }],
        )
    )
    result = await fetch_tvmaze_poster("Breaking Bad")
    assert result["coverUrl"] == "http://img/orig.jpg"
    assert result["genres"] == ["Drama"]
    assert result["description"] == "A great show."


@respx.mock
@pytest.mark.asyncio
async def test_tvmaze_url_encodes_the_title():
    """An unencoded '&' silently truncated the query."""
    route = respx.get(f"{TVMAZE}/search/shows").mock(return_value=httpx.Response(200, json=[]))
    await fetch_tvmaze_poster("Tom & Jerry")
    assert "Tom%20%26%20Jerry" in str(route.calls[0].request.url)


@respx.mock
@pytest.mark.asyncio
async def test_tvmaze_raises_on_rate_limit():
    respx.get(f"{TVMAZE}/search/shows").mock(return_value=httpx.Response(429))
    with pytest.raises(Exception, match="TVMaze API error 429"):
        await fetch_tvmaze_poster("Anything")


@respx.mock
@pytest.mark.asyncio
async def test_tvmaze_returns_empty_on_client_error():
    respx.get(f"{TVMAZE}/search/shows").mock(return_value=httpx.Response(404))
    result = await fetch_tvmaze_poster("Nope")
    assert result["coverUrl"] is None


@respx.mock
@pytest.mark.asyncio
async def test_tvmaze_handles_empty_result_list():
    respx.get(f"{TVMAZE}/search/shows").mock(return_value=httpx.Response(200, json=[]))
    result = await fetch_tvmaze_poster("Unknown Show")
    assert result["coverUrl"] is None
    assert result["genres"] == []


# ═══════════════════════════════════════════════════════════════
#  Fetchers — Jikan
# ═══════════════════════════════════════════════════════════════

@respx.mock
@pytest.mark.asyncio
async def test_jikan_poster_maps_genres_and_images():
    respx.get(f"{JIKAN}/anime").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{
                "genres": [{"name": "Action"}, {"name": "Fantasy"}],
                "images": {"jpg": {"large_image_url": "http://img/large.jpg"}},
                "synopsis": "A synopsis.",
            }]},
        )
    )
    result = await fetch_jikan_poster("Naruto")
    assert result["coverUrl"] == "http://img/large.jpg"
    assert result["genres"] == ["Action", "Fantasy"]


@respx.mock
@pytest.mark.asyncio
async def test_jikan_raises_on_server_error():
    respx.get(f"{JIKAN}/anime").mock(return_value=httpx.Response(503))
    with pytest.raises(Exception, match="Jikan API error 503"):
        await fetch_jikan_poster("Anything")


@respx.mock
@pytest.mark.asyncio
async def test_anime_runtime_parses_hours_and_minutes():
    respx.get(f"{JIKAN}/anime").mock(
        return_value=httpx.Response(
            200, json={"data": [{"duration": "1 hr 30 min", "episodes": 2}]}
        )
    )
    assert await fetch_anime_runtime("Movie Anime") == 180


@respx.mock
@pytest.mark.asyncio
async def test_anime_runtime_parses_minutes_only():
    respx.get(f"{JIKAN}/anime").mock(
        return_value=httpx.Response(
            200, json={"data": [{"duration": "24 min per ep", "episodes": 12}]}
        )
    )
    assert await fetch_anime_runtime("Series Anime") == 288


@respx.mock
@pytest.mark.asyncio
async def test_anime_runtime_returns_none_when_absent():
    respx.get(f"{JIKAN}/anime").mock(return_value=httpx.Response(200, json={"data": []}))
    assert await fetch_anime_runtime("Nothing") is None


# ═══════════════════════════════════════════════════════════════
#  Fetchers — TMDB
# ═══════════════════════════════════════════════════════════════

@respx.mock
@pytest.mark.asyncio
async def test_tmdb_movie_poster_maps_genre_ids():
    respx.get(f"{TMDB}/search/movie").mock(
        return_value=httpx.Response(200, json={"results": [_movie_result()]})
    )
    result = await fetch_tmdb_movie_poster("The Matrix", 1999)
    assert result["tmdbId"] == 603
    assert "Action" in result["genres"]
    assert result["coverUrl"].endswith("/poster.jpg")


@respx.mock
@pytest.mark.asyncio
async def test_tmdb_movie_poster_handles_no_results():
    respx.get(f"{TMDB}/search/movie").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    result = await fetch_tmdb_movie_poster("Nonexistent", None)
    assert result["coverUrl"] is None
    assert result["tmdbId"] is None


@respx.mock
@pytest.mark.asyncio
async def test_movie_runtime_follows_up_with_details_call():
    respx.get(f"{TMDB}/search/movie").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 603}]})
    )
    respx.get(f"{TMDB}/movie/603").mock(
        return_value=httpx.Response(200, json={"runtime": 136})
    )
    assert await fetch_movie_runtime("The Matrix", 1999) == 136


@respx.mock
@pytest.mark.asyncio
async def test_tv_runtime_multiplies_episode_runtime_by_count():
    respx.get(f"{TMDB}/search/tv").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 1396}]})
    )
    respx.get(f"{TMDB}/tv/1396").mock(
        return_value=httpx.Response(
            200, json={"episode_run_time": [45], "number_of_episodes": 62}
        )
    )
    assert await fetch_tv_runtime("Breaking Bad", 2008) == 45 * 62


@respx.mock
@pytest.mark.asyncio
async def test_tv_runtime_falls_back_to_default_when_unknown():
    respx.get(f"{TMDB}/search/tv").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 9}]})
    )
    respx.get(f"{TMDB}/tv/9").mock(
        return_value=httpx.Response(
            200, json={"episode_run_time": [], "number_of_episodes": 4}
        )
    )
    # No per-episode data and no last_episode_to_air -> 25 min default.
    assert await fetch_tv_runtime("Mystery Show", None) == 100


# ═══════════════════════════════════════════════════════════════
#  Search endpoint
# ═══════════════════════════════════════════════════════════════

@respx.mock
@pytest.mark.asyncio
async def test_search_endpoint_returns_results(client):
    respx.get(f"{TMDB}/search/movie").mock(
        return_value=httpx.Response(200, json={"results": [_movie_result()]})
    )
    res = await client.get("/api/v1/media/search?query=matrix&type=movie")
    assert res.status_code == 200
    results = res.json()["results"]
    assert results[0]["title"] == "The Matrix"


@respx.mock
@pytest.mark.asyncio
async def test_search_endpoint_url_encodes_query(client):
    route = respx.get(f"{TMDB}/search/movie").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await client.get("/api/v1/media/search?query=Tom %26 Jerry&type=movie")
    assert "%26" in str(route.calls[0].request.url)


@respx.mock
@pytest.mark.asyncio
async def test_search_endpoint_degrades_to_empty_on_upstream_error(client):
    respx.get(f"{TMDB}/search/movie").mock(return_value=httpx.Response(500))
    res = await client.get("/api/v1/media/search?query=boom&type=movie")
    assert res.status_code == 200
    assert res.json()["results"] == []


@respx.mock
@pytest.mark.asyncio
async def test_search_endpoint_survives_network_failure(client):
    respx.get(f"{TMDB}/search/movie").mock(side_effect=httpx.ConnectError("down"))
    res = await client.get("/api/v1/media/search?query=offline&type=movie")
    assert res.status_code == 200
    assert res.json()["results"] == []


@respx.mock
@pytest.mark.asyncio
async def test_search_anime_filters_to_animation_genre(client):
    respx.get(f"{TMDB}/search/tv").mock(
        return_value=httpx.Response(
            200,
            json={"results": [
                _tv_result(id=1, name="Real Anime", genre_ids=[16]),
                _tv_result(id=2, name="Live Action", genre_ids=[18]),
            ]},
        )
    )
    res = await client.get("/api/v1/media/search?query=x&type=anime")
    titles = [r["title"] for r in res.json()["results"]]
    assert titles == ["Real Anime"]


@pytest.mark.asyncio
async def test_search_rejects_invalid_type(client):
    res = await client.get("/api/v1/media/search?query=x&type=bogus")
    assert res.status_code == 422


@respx.mock
@pytest.mark.asyncio
async def test_search_second_call_is_served_from_cache(client):
    route = respx.get(f"{TMDB}/search/movie").mock(
        return_value=httpx.Response(200, json={"results": [_movie_result()]})
    )
    await client.get("/api/v1/media/search?query=cached&type=movie")
    await client.get("/api/v1/media/search?query=cached&type=movie")
    assert route.call_count == 1


# ═══════════════════════════════════════════════════════════════
#  Discovery endpoints
# ═══════════════════════════════════════════════════════════════

@respx.mock
@pytest.mark.asyncio
async def test_trending_movies(client):
    respx.get(f"{TMDB}/trending/movie/day").mock(
        return_value=httpx.Response(200, json={"results": [_movie_result()]})
    )
    res = await client.get("/api/v1/media/trending?type=movie")
    assert res.json()["results"][0]["tmdbId"] == 603


@respx.mock
@pytest.mark.asyncio
async def test_trending_anime_uses_discover_with_japanese_animation(client):
    route = respx.get(f"{TMDB}/discover/tv").mock(
        return_value=httpx.Response(200, json={"results": [_tv_result()]})
    )
    res = await client.get("/api/v1/media/trending?type=anime")
    assert res.status_code == 200
    url = str(route.calls[0].request.url)
    assert "with_genres=16" in url and "with_original_language=ja" in url


@respx.mock
@pytest.mark.asyncio
async def test_popular_tv(client):
    respx.get(f"{TMDB}/tv/popular").mock(
        return_value=httpx.Response(200, json={"results": [_tv_result()]})
    )
    res = await client.get("/api/v1/media/popular?type=tv")
    assert res.json()["results"][0]["title"] == "Breaking Bad"


@respx.mock
@pytest.mark.asyncio
async def test_top_rated_movies(client):
    respx.get(f"{TMDB}/movie/top_rated").mock(
        return_value=httpx.Response(200, json={"results": [_movie_result()]})
    )
    res = await client.get("/api/v1/media/top-rated?type=movie")
    assert len(res.json()["results"]) == 1


@respx.mock
@pytest.mark.asyncio
async def test_discover_translates_genre_name_to_id(client):
    route = respx.get(f"{TMDB}/discover/movie").mock(
        return_value=httpx.Response(200, json={"results": [_movie_result()]})
    )
    res = await client.get("/api/v1/media/discover?type=movie&genre=Horror")
    assert res.status_code == 200
    assert "with_genres=27" in str(route.calls[0].request.url)


@respx.mock
@pytest.mark.asyncio
async def test_discover_expands_decade_into_date_range(client):
    route = respx.get(f"{TMDB}/discover/movie").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await client.get("/api/v1/media/discover?type=movie&decade=1990")
    url = str(route.calls[0].request.url)
    assert "1990-01-01" in url and "1999-12-31" in url


@respx.mock
@pytest.mark.asyncio
async def test_discover_degrades_on_error(client):
    respx.get(f"{TMDB}/discover/movie").mock(side_effect=httpx.ConnectError("down"))
    res = await client.get("/api/v1/media/discover?type=movie")
    assert res.status_code == 200
    assert res.json()["results"] == []


@pytest.mark.asyncio
async def test_discover_rejects_out_of_range_page(client):
    res = await client.get("/api/v1/media/discover?type=movie&page=9999")
    assert res.status_code == 422


# ═══════════════════════════════════════════════════════════════
#  Cache-first poster / runtime endpoints
# ═══════════════════════════════════════════════════════════════

@respx.mock
@pytest.mark.asyncio
async def test_poster_endpoint_populates_then_serves_cache(client):
    route = respx.get(f"{TMDB}/search/movie").mock(
        return_value=httpx.Response(200, json={"results": [_movie_result()]})
    )
    first = await client.get("/api/v1/media/poster?title=The Matrix&type=movie&year=1999")
    assert first.json()["coverUrl"].endswith("/poster.jpg")

    second = await client.get("/api/v1/media/poster?title=The Matrix&type=movie&year=1999")
    assert second.json()["coverUrl"].endswith("/poster.jpg")
    # Second request served from the DB cache, not TMDB.
    assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_runtime_endpoint_populates_then_serves_cache(client):
    respx.get(f"{TMDB}/search/movie").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 603}]})
    )
    details = respx.get(f"{TMDB}/movie/603").mock(
        return_value=httpx.Response(200, json={"runtime": 136})
    )
    first = await client.get("/api/v1/media/runtime?title=Matrix Runtime&type=movie&year=1999")
    assert first.json()["runtime"] == 136

    second = await client.get("/api/v1/media/runtime?title=Matrix Runtime&type=movie&year=1999")
    assert second.json()["runtime"] == 136
    assert details.call_count == 1


# ═══════════════════════════════════════════════════════════════
#  Details endpoint
# ═══════════════════════════════════════════════════════════════

@respx.mock
@pytest.mark.asyncio
async def test_details_aggregates_credits_and_trailer(client):
    respx.get(f"{TMDB}/movie/603").mock(
        return_value=httpx.Response(200, json={
            "title": "The Matrix",
            "release_date": "1999-03-30",
            "overview": "Hacker.",
            "genres": [{"name": "Action"}],
            "vote_average": 8.2,
            "vote_count": 100,
            "poster_path": "/p.jpg",
            "backdrop_path": "/b.jpg",
            "runtime": 136,
        })
    )
    respx.get(f"{TMDB}/movie/603/credits").mock(
        return_value=httpx.Response(200, json={
            "cast": [{"name": "Keanu Reeves", "character": "Neo", "profile_path": "/k.jpg"}],
            "crew": [
                {"name": "Lana Wachowski", "job": "Director"},
                {"name": "Someone", "job": "Screenplay"},
            ],
        })
    )
    respx.get(f"{TMDB}/movie/603/videos").mock(
        return_value=httpx.Response(200, json={
            "results": [{"site": "YouTube", "type": "Trailer", "key": "abc123"}]
        })
    )

    res = await client.get("/api/v1/media/details?tmdb_id=603&type=movie")
    data = res.json()
    assert data["directors"] == ["Lana Wachowski"]
    assert data["writers"] == ["Someone"]
    assert data["cast"][0]["name"] == "Keanu Reeves"
    assert data["trailerKey"] == "abc123"
    assert data["year"] == 1999


@respx.mock
@pytest.mark.asyncio
async def test_details_propagates_not_found(client):
    respx.get(f"{TMDB}/movie/999999").mock(return_value=httpx.Response(404))
    res = await client.get("/api/v1/media/details?tmdb_id=999999&type=movie")
    assert res.status_code == 404


@respx.mock
@pytest.mark.asyncio
async def test_details_maps_upstream_failure_to_502(client):
    respx.get(f"{TMDB}/movie/603").mock(return_value=httpx.Response(500))
    res = await client.get("/api/v1/media/details?tmdb_id=603&type=movie")
    assert res.status_code == 502


@respx.mock
@pytest.mark.asyncio
async def test_details_tolerates_missing_credits_and_videos(client):
    respx.get(f"{TMDB}/tv/1396").mock(
        return_value=httpx.Response(200, json={
            "name": "Breaking Bad",
            "first_air_date": "2008-01-20",
            "genres": [],
            "episode_run_time": [45],
        })
    )
    respx.get(f"{TMDB}/tv/1396/credits").mock(return_value=httpx.Response(500))
    respx.get(f"{TMDB}/tv/1396/videos").mock(return_value=httpx.Response(500))

    res = await client.get("/api/v1/media/details?tmdb_id=1396&type=tv")
    data = res.json()
    assert data["title"] == "Breaking Bad"
    assert data["cast"] == []
    assert data["trailerKey"] is None
    assert data["runtime"] == 45
