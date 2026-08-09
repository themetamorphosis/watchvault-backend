"""Media search, discovery, and cache endpoints.

Handles autocomplete search, trending/popular/top-rated lists,
custom discovery, and cache-first poster/runtime lookups.
All business logic delegates to app.services.media_service.
"""

from fastapi import APIRouter, Query, Depends
from typing import Optional
import logging
import urllib.parse

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.media import PosterResponse, RuntimeResponse, TMDBSearchResponse
from app.core.config import settings
from app.api.dependencies import get_db
from app.services.media_service import (
    get_cached,
    fetch_and_cache_poster,
    fetch_and_cache_runtime,
    cache_get,
    cache_set,
    get_shared_client,
    TMDB_GENRES,
    TMDB_TV_GENRES,
    parse_tmdb_results,
)

router = APIRouter()
logger = logging.getLogger(__name__)
# ═══════════════════════════════════════════════════════════════
#  CACHE-FIRST ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@router.get("/poster", response_model=PosterResponse)
async def get_poster(
    title: str = Query(..., min_length=1),
    type: str = Query("movie"),
    year: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get poster + genres for a title. Checks DB cache first."""
    cached = await get_cached(db, title, type, year)
    if cached and cached.coverUrl:
        logger.info(f"CACHE HIT (poster): {title}")
        return PosterResponse(ok=True, coverUrl=cached.coverUrl, genres=cached.genres or [], description=cached.description)

    logger.info(f"CACHE MISS (poster): {title} — fetching from external API")
    result = await fetch_and_cache_poster(db, title, type, year)
    return PosterResponse(ok=True, coverUrl=result.get("coverUrl"), genres=result.get("genres", []), description=result.get("description"))


@router.get("/runtime", response_model=RuntimeResponse)
async def get_runtime(
    title: str = Query(..., min_length=1),
    type: str = Query("movie"),
    year: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get runtime for a title. Checks DB cache first."""
    cached = await get_cached(db, title, type, year)
    if cached and cached.runtime is not None:
        logger.info(f"CACHE HIT (runtime): {title}")
        return RuntimeResponse(runtime=cached.runtime)

    logger.info(f"CACHE MISS (runtime): {title} — fetching from external API")
    runtime = await fetch_and_cache_runtime(db, title, type, year)
    return RuntimeResponse(runtime=runtime)


# ═══════════════════════════════════════════════════════════════
#  TMDB SEARCH (autocomplete)
# ═══════════════════════════════════════════════════════════════

@router.get("/search", response_model=TMDBSearchResponse)
async def search_tmdb(
    query: str = Query(..., min_length=1, max_length=200),
    type: str = Query("movie", pattern="^(movie|tv|anime)$"),
):
    """Search TMDB for movies or TV shows. Returns up to 8 results for autocomplete."""
    if not settings.TMDB_API_KEY:
        return TMDBSearchResponse(results=[])

    cache_key = f"{type}:{query.strip().lower()}"
    cached = cache_get(cache_key)
    if cached is not None:
        return TMDBSearchResponse(results=cached)

    tmdb_type = "tv" if type in ("tv", "anime") else "movie"
    genre_map = TMDB_TV_GENRES if tmdb_type == "tv" else TMDB_GENRES

    encoded_query = urllib.parse.quote(query)
    url = (
        f"https://api.themoviedb.org/3/search/{tmdb_type}"
        f"?api_key={settings.TMDB_API_KEY}"
        f"&query={encoded_query}"
        f"&include_adult=false&page=1"
    )

    try:
        client = get_shared_client()
        r = await client.get(url)
        if r.status_code != 200:
            logger.warning(f"TMDB search failed ({r.status_code}) for query='{query}'")
            return TMDBSearchResponse(results=[])

        raw_results = r.json().get("results", [])

        # For anime, filter to animation genre (id=16)
        if type == "anime":
            raw_results = [
                r for r in raw_results
                if 16 in r.get("genre_ids", [])
            ]

        from app.schemas.media import TMDBSearchResult
        results = []
        for item in raw_results[:8]:
            if tmdb_type == "movie":
                title = item.get("title", "")
                date_str = item.get("release_date", "")
            else:
                title = item.get("name", "")
                date_str = item.get("first_air_date", "")

            year = int(date_str[:4]) if date_str and len(date_str) >= 4 else None
            poster_path = item.get("poster_path")
            backdrop_path = item.get("backdrop_path")
            genres = [genre_map[g] for g in item.get("genre_ids", []) if g in genre_map]

            results.append(TMDBSearchResult(
                tmdbId=item.get("id", 0),
                title=title,
                year=year,
                posterUrl=f"https://image.tmdb.org/t/p/w185{poster_path}" if poster_path else None,
                backdropUrl=f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else None,
                overview=item.get("overview"),
                mediaType=type,
                genres=genres,
                voteAverage=item.get("vote_average"),
            ))

        cache_set(cache_key, results)
        return TMDBSearchResponse(results=results)

    except Exception as e:
        logger.error(f"TMDB search error for query='{query}': {e}")
        return TMDBSearchResponse(results=[])


# ═══════════════════════════════════════════════════════════════
#  TMDB TRENDING / POPULAR / TOP-RATED / DISCOVER / DETAILS
# ═══════════════════════════════════════════════════════════════

@router.get("/trending", response_model=TMDBSearchResponse)
async def get_trending(
    type: str = Query("movie", pattern="^(movie|tv|anime)$"),
    time_window: str = Query("day", pattern="^(day|week)$"),
):
    """Get trending media from TMDB."""
    if not settings.TMDB_API_KEY:
        return TMDBSearchResponse(results=[])

    cache_key = f"trending:{type}:{time_window}"
    cached = cache_get(cache_key)
    if cached is not None:
        return TMDBSearchResponse(results=cached)

    client = get_shared_client()
    results = []

    try:
        if type == "anime":
            url = (
                f"https://api.themoviedb.org/3/discover/tv"
                f"?api_key={settings.TMDB_API_KEY}"
                f"&with_genres=16&sort_by=popularity.desc"
                f"&with_original_language=ja&page=1"
            )
        else:
            tmdb_type = "tv" if type == "tv" else "movie"
            url = f"https://api.themoviedb.org/3/trending/{tmdb_type}/{time_window}?api_key={settings.TMDB_API_KEY}"

        r = await client.get(url)
        if r.status_code == 200:
            raw_results = r.json().get("results", [])
            genre_map = TMDB_TV_GENRES if type in ("tv", "anime") else TMDB_GENRES
            results = parse_tmdb_results(raw_results, type, genre_map)

        if results:
            cache_set(cache_key, results)
        return TMDBSearchResponse(results=results)

    except Exception as e:
        logger.error(f"Trending fetch error: {e}")
        return TMDBSearchResponse(results=[])


@router.get("/popular", response_model=TMDBSearchResponse)
async def get_popular(
    type: str = Query("movie", pattern="^(movie|tv|anime)$"),
    page: int = Query(1, ge=1, le=500),
):
    """Get popular media from TMDB."""
    if not settings.TMDB_API_KEY:
        return TMDBSearchResponse(results=[])

    cache_key = f"popular:{type}:{page}"
    cached = cache_get(cache_key)
    if cached is not None:
        return TMDBSearchResponse(results=cached)

    client = get_shared_client()
    results = []

    try:
        if type == "anime":
            url = (
                f"https://api.themoviedb.org/3/discover/tv"
                f"?api_key={settings.TMDB_API_KEY}"
                f"&with_genres=16&sort_by=popularity.desc"
                f"&with_original_language=ja&page={page}"
            )
        else:
            tmdb_type = "tv" if type == "tv" else "movie"
            url = f"https://api.themoviedb.org/3/{tmdb_type}/popular?api_key={settings.TMDB_API_KEY}&page={page}"

        r = await client.get(url)
        if r.status_code == 200:
            raw_results = r.json().get("results", [])
            genre_map = TMDB_TV_GENRES if type in ("tv", "anime") else TMDB_GENRES
            results = parse_tmdb_results(raw_results, type, genre_map)

        if results:
            cache_set(cache_key, results)
        return TMDBSearchResponse(results=results)

    except Exception as e:
        logger.error(f"Popular fetch error: {e}")
        return TMDBSearchResponse(results=[])


@router.get("/top-rated", response_model=TMDBSearchResponse)
async def get_top_rated(
    type: str = Query("movie", pattern="^(movie|tv|anime)$"),
    page: int = Query(1, ge=1, le=500),
):
    """Get top-rated media from TMDB."""
    if not settings.TMDB_API_KEY:
        return TMDBSearchResponse(results=[])

    cache_key = f"top-rated:{type}:{page}"
    cached = cache_get(cache_key)
    if cached is not None:
        return TMDBSearchResponse(results=cached)

    client = get_shared_client()
    results = []

    try:
        if type == "anime":
            url = (
                f"https://api.themoviedb.org/3/discover/tv"
                f"?api_key={settings.TMDB_API_KEY}"
                f"&with_genres=16&sort_by=vote_average.desc"
                f"&vote_count.gte=100&with_original_language=ja&page={page}"
            )
        else:
            tmdb_type = "tv" if type == "tv" else "movie"
            url = f"https://api.themoviedb.org/3/{tmdb_type}/top_rated?api_key={settings.TMDB_API_KEY}&page={page}"

        r = await client.get(url)
        if r.status_code == 200:
            raw_results = r.json().get("results", [])
            genre_map = TMDB_TV_GENRES if type in ("tv", "anime") else TMDB_GENRES
            results = parse_tmdb_results(raw_results, type, genre_map)

        if results:
            cache_set(cache_key, results)
        return TMDBSearchResponse(results=results)

    except Exception as e:
        logger.error(f"Top-rated fetch error: {e}")
        return TMDBSearchResponse(results=[])


@router.get("/discover", response_model=TMDBSearchResponse)
async def discover_media(
    type: str = Query("movie", pattern="^(movie|tv|anime)$"),
    genre: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    decade: Optional[int] = Query(None),
    # Allow-listed: this value is concatenated into the TMDB query string, so
    # free text let a caller append arbitrary extra TMDB parameters.
    sort_by: str = Query(
        "popularity.desc",
        pattern=r"^(popularity|vote_average|vote_count|revenue|primary_release_date|first_air_date|original_title|name)\.(asc|desc)$",
    ),
    page: int = Query(1, ge=1, le=500),
):
    """Discover media with advanced filters."""
    if not settings.TMDB_API_KEY:
        return TMDBSearchResponse(results=[])

    cache_key = f"discover:{type}:{genre}:{year}:{decade}:{sort_by}:{page}"
    cached = cache_get(cache_key)
    if cached is not None:
        return TMDBSearchResponse(results=cached)

    tmdb_type = "tv" if type in ("tv", "anime") else "movie"
    genre_map = TMDB_TV_GENRES if tmdb_type == "tv" else TMDB_GENRES
    client = get_shared_client()
    results = []

    url_params = [
        f"api_key={settings.TMDB_API_KEY}",
        f"sort_by={sort_by}",
        "include_adult=false",
        f"page={page}",
    ]

    if genre:
        # Look up genre ID from name
        reverse_map = {v.lower(): k for k, v in genre_map.items()}
        genre_id = reverse_map.get(genre.lower())
        if genre_id:
            url_params.append(f"with_genres={genre_id}")

    if type == "anime":
        url_params.append("with_genres=16")
        url_params.append("with_original_language=ja")

    if tmdb_type == "movie":
        if year:
            url_params.append(f"primary_release_year={year}")
        elif decade:
            url_params.append(f"primary_release_date.gte={decade}-01-01")
            url_params.append(f"primary_release_date.lte={decade + 9}-12-31")
    else:
        if year:
            url_params.append(f"first_air_date_year={year}")
        elif decade:
            url_params.append(f"first_air_date.gte={decade}-01-01")
            url_params.append(f"first_air_date.lte={decade + 9}-12-31")

    url = f"https://api.themoviedb.org/3/discover/{tmdb_type}?" + "&".join(url_params)

    try:
        r = await client.get(url)
        if r.status_code == 200:
            raw_results = r.json().get("results", [])
            results = parse_tmdb_results(raw_results, type, genre_map)

        if results:
            cache_set(cache_key, results)
        return TMDBSearchResponse(results=results)

    except Exception as e:
        logger.error(f"Discover fetch error: {e}")
        return TMDBSearchResponse(results=[])


