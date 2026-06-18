"""Media endpoints with database-level cache-aside pattern.

Every external API call (TMDB, TVMaze, Jikan) goes through the MediaCache table.
On cache hit → return instantly from DB (0ms external latency).
On cache miss → fetch from external API, store in cache, then return.
"""

from fastapi import APIRouter, Query, Depends
from typing import Optional
import httpx
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.schemas.media import PosterResponse, RuntimeResponse, TMDBSearchResult, TMDBSearchResponse
from app.core.config import settings
from app.api.dependencies import get_db
from app.db import models

router = APIRouter()
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  CACHE LAYER
# ═══════════════════════════════════════════════════════════════

async def get_cached(db: AsyncSession, title: str, media_type: str, year: Optional[int]) -> Optional[models.MediaCache]:
    """Look up a cache entry by (title, mediaType, year)."""
    q = select(models.MediaCache).filter(
        models.MediaCache.title == title,
        models.MediaCache.mediaType == media_type,
    )
    if year is not None:
        q = q.filter(models.MediaCache.year == year)
    else:
        q = q.filter(models.MediaCache.year.is_(None))
    result = await db.execute(q)
    return result.scalars().first()


async def upsert_cache(
    db: AsyncSession,
    title: str,
    media_type: str,
    year: Optional[int],
    cover_url: Optional[str] = None,
    genres: Optional[list] = None,
    description: Optional[str] = None,
    runtime: Optional[int] = None,
    tmdb_id: Optional[int] = None,
) -> models.MediaCache:
    """Insert or update a cache entry."""
    existing = await get_cached(db, title, media_type, year)
    if existing:
        # Merge new data into existing entry (don't overwrite with None)
        if cover_url is not None and not existing.coverUrl:
            existing.coverUrl = cover_url
        if genres and (not existing.genres or len(existing.genres) == 0):
            existing.genres = genres
        if description is not None and not existing.description:
            existing.description = description
        if runtime is not None and existing.runtime is None:
            existing.runtime = runtime
        if tmdb_id is not None and existing.tmdbId is None:
            existing.tmdbId = tmdb_id
        await db.commit()
        await db.refresh(existing)
        return existing
    else:
        entry = models.MediaCache(
            id=str(uuid.uuid4()),
            title=title,
            mediaType=media_type,
            year=year,
            coverUrl=cover_url,
            genres=genres or [],
            description=description,
            runtime=runtime,
            tmdbId=tmdb_id,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return entry


# ═══════════════════════════════════════════════════════════════
#  EXTERNAL API FETCHERS (unchanged logic, extracted cleanly)
# ═══════════════════════════════════════════════════════════════

async def _fetch_tvmaze_poster(title: str) -> dict:
    client = _get_shared_client()
    r = await client.get(f"https://api.tvmaze.com/search/shows?q={title}")
    if r.status_code == 429 or r.status_code >= 500:
        raise Exception(f"TVMaze API error {r.status_code}")
    if r.status_code != 200:
        return {"coverUrl": None, "genres": [], "description": None}
    data = r.json()
    if not data:
        return {"coverUrl": None, "genres": []}
    show = data[0].get("show", {})
    images = show.get("image") or {}

    import re
    raw_summary = show.get("summary") or ""
    clean_summary = re.sub('<[^<]+?>', '', raw_summary) if raw_summary else None

    return {
        "coverUrl": images.get("original") or images.get("medium"),
        "genres": show.get("genres", []),
        "description": clean_summary,
    }

async def _fetch_jikan_poster(title: str) -> dict:
    client = _get_shared_client()
    r = await client.get(f"https://api.jikan.moe/v4/anime?q={title}&limit=1")
    if r.status_code == 429 or r.status_code >= 500:
        raise Exception(f"Jikan API error {r.status_code}")
    if r.status_code != 200:
        return {"coverUrl": None, "genres": [], "description": None}
    data = r.json().get("data", [])
    if not data:
        return {"coverUrl": None, "genres": []}
    anime = data[0]
    genres = [g.get("name") for g in anime.get("genres", [])]
    images = anime.get("images", {}).get("jpg", {})
    return {
        "coverUrl": images.get("large_image_url") or images.get("image_url"),
        "genres": genres,
        "description": anime.get("synopsis"),
    }

TMDB_GENRES = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History",
    27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance", 878: "Science Fiction",
    10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
}

async def _fetch_tmdb_movie_poster(title: str, year: Optional[int]) -> dict:
    if not settings.TMDB_API_KEY:
        return {"coverUrl": None, "genres": [], "tmdbId": None}
    url = f"https://api.themoviedb.org/3/search/movie?api_key={settings.TMDB_API_KEY}&query={title}&include_adult=false&page=1"
    if year:
        url += f"&year={year}"
    client = _get_shared_client()
    r = await client.get(url)
    if r.status_code != 200:
        return {"coverUrl": None, "genres": [], "tmdbId": None}
    results = r.json().get("results", [])
    if not results:
        return {"coverUrl": None, "genres": [], "tmdbId": None}
    result = results[0]
    genres = [TMDB_GENRES[g] for g in result.get("genre_ids", []) if g in TMDB_GENRES]
    poster_path = result.get("poster_path")
    return {
        "coverUrl": f"https://image.tmdb.org/t/p/w780{poster_path}" if poster_path else None,
        "genres": genres,
        "description": result.get("overview"),
        "tmdbId": result.get("id"),
    }


async def _fetch_movie_runtime(title: str, year: Optional[int]) -> Optional[int]:
    if not settings.TMDB_API_KEY:
        return None
    url = f"https://api.themoviedb.org/3/search/movie?api_key={settings.TMDB_API_KEY}&query={title}&include_adult=false&page=1"
    if year:
        url += f"&year={year}"
    client = _get_shared_client()
    r = await client.get(url)
    if r.status_code != 200:
        return None
    results = r.json().get("results", [])
    if not results:
        return None
    movie_id = results[0].get("id")
    d_req = await client.get(f"https://api.themoviedb.org/3/movie/{movie_id}?api_key={settings.TMDB_API_KEY}")
    if d_req.status_code != 200:
        return None
    return d_req.json().get("runtime")

async def _fetch_tv_runtime(title: str, year: Optional[int]) -> Optional[int]:
    if not settings.TMDB_API_KEY:
        return None
    url = f"https://api.themoviedb.org/3/search/tv?api_key={settings.TMDB_API_KEY}&query={title}&include_adult=false&page=1"
    if year:
        url += f"&first_air_date_year={year}"
    client = _get_shared_client()
    r = await client.get(url)
    if r.status_code != 200:
        return None
    results = r.json().get("results", [])
    if not results:
        return None
    show_id = results[0].get("id")
    d_req = await client.get(f"https://api.themoviedb.org/3/tv/{show_id}?api_key={settings.TMDB_API_KEY}")
    if d_req.status_code != 200:
        return None
    info = d_req.json()
    ep_runtimes = info.get("episode_run_time") or []
    last_episode = info.get("last_episode_to_air") or {}
    ep_runtime = sum(ep_runtimes) / len(ep_runtimes) if ep_runtimes else (last_episode.get("runtime") or 25)
    total_eps = info.get("number_of_episodes") or 1
    return int(round(ep_runtime * total_eps))

async def _fetch_anime_runtime(title: str) -> Optional[int]:
    import re
    client = _get_shared_client()
    r = await client.get(f"https://api.jikan.moe/v4/anime?q={title}&limit=1")
    if r.status_code != 200:
        return None
    data = r.json().get("data", [])
    if not data:
        return None
    anime = data[0]
    duration_str = anime.get("duration", "")
    episodes = anime.get("episodes") or 1
    min_per_ep = 24
    if "hr" in duration_str:
        m = re.search(r'(\d+)\s*hr', duration_str)
        if m:
            min_per_ep = int(m.group(1)) * 60
        m2 = re.search(r'(\d+)\s*min', duration_str)
        if m2:
            min_per_ep += int(m2.group(1))
    else:
        m = re.search(r'(\d+)\s*min', duration_str)
        if m:
            min_per_ep = int(m.group(1))
    return int(round(min_per_ep * episodes))


# ═══════════════════════════════════════════════════════════════
#  COMBINED FETCH + CACHE HELPERS
# ═══════════════════════════════════════════════════════════════

async def fetch_and_cache_poster(db: AsyncSession, title: str, media_type: str, year: Optional[int]) -> dict:
    """Fetch poster data from external API and store in cache."""
    try:
        if media_type == "tv":
            result = await _fetch_tvmaze_poster(title)
        elif media_type == "anime":
            result = await _fetch_jikan_poster(title)
        else:
            result = await _fetch_tmdb_movie_poster(title, year)

        # Store in cache
        await upsert_cache(
            db, title, media_type, year,
            cover_url=result.get("coverUrl"),
            genres=result.get("genres", []),
            description=result.get("description"),
            tmdb_id=result.get("tmdbId"),
        )
        return result
    except Exception as e:
        logger.warning(f"External API fetch failed for poster '{title}': {e}")
        return {"coverUrl": None, "genres": [], "description": None}


async def fetch_and_cache_runtime(db: AsyncSession, title: str, media_type: str, year: Optional[int]) -> Optional[int]:
    """Fetch runtime from external API and store in cache."""
    try:
        runtime = None
        if media_type == "anime":
            runtime = await _fetch_anime_runtime(title)
            if runtime is None:
                runtime = await _fetch_tv_runtime(title, year)
        elif media_type == "tv":
            runtime = await _fetch_tv_runtime(title, year)
        else:
            runtime = await _fetch_movie_runtime(title, year)

        if runtime is not None:
            await upsert_cache(db, title, media_type, year, runtime=runtime)
        return runtime
    except Exception as e:
        logger.warning(f"External API fetch failed for runtime '{title}': {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  PUBLIC: Background enrichment function (used by watchlist)
# ═══════════════════════════════════════════════════════════════

async def enrich_media_cache(db: AsyncSession, title: str, media_type: str, year: Optional[int]):
    """Ensure a cache entry exists with poster + runtime. Used as a background task."""
    cached = await get_cached(db, title, media_type, year)

    needs_poster = not cached or not cached.coverUrl
    needs_runtime = not cached or cached.runtime is None

    if needs_poster:
        await fetch_and_cache_poster(db, title, media_type, year)

    if needs_runtime:
        await fetch_and_cache_runtime(db, title, media_type, year)


# ═══════════════════════════════════════════════════════════════
#  API ENDPOINTS (cache-first)
# ═══════════════════════════════════════════════════════════════

@router.get("/poster", response_model=PosterResponse)
async def get_poster(
    title: str = Query(..., min_length=1),
    type: str = Query("movie"),
    year: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get poster + genres for a title. Checks DB cache first."""
    # 1. Check cache
    cached = await get_cached(db, title, type, year)
    if cached and cached.coverUrl:
        logger.info(f"CACHE HIT (poster): {title}")
        return PosterResponse(ok=True, coverUrl=cached.coverUrl, genres=cached.genres or [], description=cached.description)

    # 2. Cache miss → fetch from external API
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
    # 1. Check cache
    cached = await get_cached(db, title, type, year)
    if cached and cached.runtime is not None:
        logger.info(f"CACHE HIT (runtime): {title}")
        return RuntimeResponse(runtime=cached.runtime)

    # 2. Cache miss → fetch from external API
    logger.info(f"CACHE MISS (runtime): {title} — fetching from external API")
    runtime = await fetch_and_cache_runtime(db, title, type, year)
    return RuntimeResponse(runtime=runtime)


# ═══════════════════════════════════════════════════════════════
#  TMDB SEARCH (autocomplete suggestions) — optimised
# ═══════════════════════════════════════════════════════════════

TMDB_TV_GENRES = {
    10759: "Action & Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 10762: "Kids",
    9648: "Mystery", 10763: "News", 10764: "Reality", 10765: "Sci-Fi & Fantasy",
    10766: "Soap", 10767: "Talk", 10768: "War & Politics", 37: "Western",
}

# ── Persistent HTTP client (reuses TCP connections for all external APIs) ──
_shared_client: httpx.AsyncClient | None = None

def _get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=10.0,
            http2=False,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _shared_client

# ── Simple in-memory cache (key → (timestamp, results)) ─────
import time as _time
_search_cache: dict[str, tuple[float, list]] = {}
_CACHE_TTL = 300  # 5 minutes
_CACHE_MAX = 128


def _cache_get(key: str):
    entry = _search_cache.get(key)
    if entry and (_time.time() - entry[0]) < _CACHE_TTL:
        return entry[1]
    if entry:
        _search_cache.pop(key, None)
    return None


def _cache_set(key: str, value: list):
    # Evict oldest if full
    if len(_search_cache) >= _CACHE_MAX:
        oldest = min(_search_cache, key=lambda k: _search_cache[k][0])
        _search_cache.pop(oldest, None)
    _search_cache[key] = (_time.time(), value)


@router.get("/search", response_model=TMDBSearchResponse)
async def search_tmdb(
    query: str = Query(..., min_length=1, max_length=200),
    type: str = Query("movie", pattern="^(movie|tv|anime)$"),
):
    """Search TMDB for movies or TV shows. Returns up to 8 results for autocomplete."""
    if not settings.TMDB_API_KEY:
        return TMDBSearchResponse(results=[])

    # ── Check cache first ────────────────────────────────────
    cache_key = f"{type}:{query.strip().lower()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return TMDBSearchResponse(results=cached)

    # Anime uses TMDB TV search (animation genre)
    tmdb_type = "tv" if type in ("tv", "anime") else "movie"
    genre_map = TMDB_TV_GENRES if tmdb_type == "tv" else TMDB_GENRES

    import urllib.parse
    encoded_query = urllib.parse.quote(query)
    url = (
        f"https://api.themoviedb.org/3/search/{tmdb_type}"
        f"?api_key={settings.TMDB_API_KEY}"
        f"&query={encoded_query}"
        f"&include_adult=false&page=1"
    )

    try:
        client = _get_shared_client()
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

        results = []
        for item in raw_results[:8]:
            # Movie: title + release_date | TV: name + first_air_date
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
                mediaType=type,  # Keep original type (movie/tv/anime)
                genres=genres,
                voteAverage=item.get("vote_average"),
            ))

        # ── Cache the results ────────────────────────────────
        _cache_set(cache_key, results)
        return TMDBSearchResponse(results=results)

    except Exception as e:
        logger.error(f"TMDB search error for query='{query}': {e}")
        return TMDBSearchResponse(results=[])


# Helper function to parse raw TMDB results consistently
def _parse_tmdb_results(raw_results: list, type: str, genre_map: dict) -> list[TMDBSearchResult]:
    results = []
    tmdb_type = "tv" if type in ("tv", "anime") else "movie"
    for item in raw_results:
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
            posterUrl=f"https://image.tmdb.org/t/p/w342{poster_path}" if poster_path else None,
            backdropUrl=f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else None,
            overview=item.get("overview"),
            mediaType=type,
            genres=genres,
            voteAverage=item.get("vote_average"),
        ))
    return results


@router.get("/trending", response_model=TMDBSearchResponse)
async def get_trending(
    type: str = Query("movie", pattern="^(movie|tv|anime)$"),
    time_window: str = Query("day", pattern="^(day|week)$"),
):
    """Get trending media (movies, TV shows, or anime) from TMDB."""
    if not settings.TMDB_API_KEY:
        return TMDBSearchResponse(results=[])

    cache_key = f"trending:{type}:{time_window}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return TMDBSearchResponse(results=cached)

    client = _get_shared_client()
    results = []

    try:
        if type == "anime":
            url = (
                f"https://api.themoviedb.org/3/discover/tv"
                f"?api_key={settings.TMDB_API_KEY}"
                f"&with_genres=16"
                f"&with_original_language=ja"
                f"&sort_by=popularity.desc"
                f"&page=1"
            )
            r = await client.get(url)
            if r.status_code == 200:
                raw_results = r.json().get("results", [])
                results = _parse_tmdb_results(raw_results, "anime", TMDB_TV_GENRES)
        else:
            tmdb_type = "movie" if type == "movie" else "tv"
            genre_map = TMDB_GENRES if type == "movie" else TMDB_TV_GENRES
            url = f"https://api.themoviedb.org/3/trending/{tmdb_type}/{time_window}?api_key={settings.TMDB_API_KEY}"
            r = await client.get(url)
            if r.status_code == 200:
                raw_results = r.json().get("results", [])
                results = _parse_tmdb_results(raw_results, type, genre_map)

        if results:
            _cache_set(cache_key, results)
        return TMDBSearchResponse(results=results)

    except Exception as e:
        logger.error(f"Trending fetch error for type={type}: {e}")
        return TMDBSearchResponse(results=[])


@router.get("/popular", response_model=TMDBSearchResponse)
async def get_popular(
    type: str = Query("movie", pattern="^(movie|tv|anime)$"),
    page: int = Query(1, ge=1, le=100),
):
    """Get popular media from TMDB."""
    if not settings.TMDB_API_KEY:
        return TMDBSearchResponse(results=[])

    cache_key = f"popular:{type}:{page}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return TMDBSearchResponse(results=cached)

    client = _get_shared_client()
    results = []

    try:
        if type == "anime":
            url = (
                f"https://api.themoviedb.org/3/discover/tv"
                f"?api_key={settings.TMDB_API_KEY}"
                f"&with_genres=16"
                f"&with_original_language=ja"
                f"&sort_by=popularity.desc"
                f"&page={page}"
            )
            r = await client.get(url)
            if r.status_code == 200:
                raw_results = r.json().get("results", [])
                results = _parse_tmdb_results(raw_results, "anime", TMDB_TV_GENRES)
        else:
            tmdb_type = "movie" if type == "movie" else "tv"
            genre_map = TMDB_GENRES if type == "movie" else TMDB_TV_GENRES
            url = f"https://api.themoviedb.org/3/{tmdb_type}/popular?api_key={settings.TMDB_API_KEY}&page={page}"
            r = await client.get(url)
            if r.status_code == 200:
                raw_results = r.json().get("results", [])
                results = _parse_tmdb_results(raw_results, type, genre_map)

        if results:
            _cache_set(cache_key, results)
        return TMDBSearchResponse(results=results)

    except Exception as e:
        logger.error(f"Popular fetch error for type={type}: {e}")
        return TMDBSearchResponse(results=[])


@router.get("/top-rated", response_model=TMDBSearchResponse)
async def get_top_rated(
    type: str = Query("movie", pattern="^(movie|tv|anime)$"),
    page: int = Query(1, ge=1, le=100),
):
    """Get top rated media from TMDB."""
    if not settings.TMDB_API_KEY:
        return TMDBSearchResponse(results=[])

    cache_key = f"top-rated:{type}:{page}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return TMDBSearchResponse(results=cached)

    client = _get_shared_client()
    results = []

    try:
        if type == "anime":
            url = (
                f"https://api.themoviedb.org/3/discover/tv"
                f"?api_key={settings.TMDB_API_KEY}"
                f"&with_genres=16"
                f"&with_original_language=ja"
                f"&sort_by=vote_average.desc"
                f"&vote_count.gte=150"
                f"&page={page}"
            )
            r = await client.get(url)
            if r.status_code == 200:
                raw_results = r.json().get("results", [])
                results = _parse_tmdb_results(raw_results, "anime", TMDB_TV_GENRES)
        else:
            tmdb_type = "movie" if type == "movie" else "tv"
            genre_map = TMDB_GENRES if type == "movie" else TMDB_TV_GENRES
            url = f"https://api.themoviedb.org/3/{tmdb_type}/top_rated?api_key={settings.TMDB_API_KEY}&page={page}"
            r = await client.get(url)
            if r.status_code == 200:
                raw_results = r.json().get("results", [])
                results = _parse_tmdb_results(raw_results, type, genre_map)

        if results:
            _cache_set(cache_key, results)
        return TMDBSearchResponse(results=results)

    except Exception as e:
        logger.error(f"Top rated fetch error for type={type}: {e}")
        return TMDBSearchResponse(results=[])


@router.get("/discover", response_model=TMDBSearchResponse)
async def discover_media(
    type: str = Query("movie", pattern="^(movie|tv|anime)$"),
    genre: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    decade: Optional[int] = Query(None),
    sort_by: str = Query("popularity.desc", pattern="^(popularity.desc|vote_average.desc|primary_release_date.desc|first_air_date.desc)$"),
    page: int = Query(1, ge=1, le=100),
):
    """Discover media from TMDB with advanced filtering and sorting."""
    if not settings.TMDB_API_KEY:
        return TMDBSearchResponse(results=[])

    cache_key = f"discover:{type}:{genre}:{year}:{decade}:{sort_by}:{page}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return TMDBSearchResponse(results=cached)

    client = _get_shared_client()
    results = []

    tmdb_type = "tv" if type in ("tv", "anime") else "movie"
    genre_map = TMDB_TV_GENRES if tmdb_type == "tv" else TMDB_GENRES
    name_to_id = {v.lower(): k for k, v in genre_map.items()}
    if type == "anime":
        name_to_id.update({v.lower(): k for k, v in TMDB_TV_GENRES.items()})

    genre_ids = []
    if genre:
        for g in genre.split(","):
            g = g.strip().lower()
            if g.isdigit():
                genre_ids.append(g)
            elif g in name_to_id:
                genre_ids.append(str(name_to_id[g]))

    if type == "anime" and "16" not in genre_ids:
        genre_ids.append("16")

    url_params = [
        f"api_key={settings.TMDB_API_KEY}",
        f"page={page}",
        f"sort_by={sort_by}",
        "include_adult=false",
    ]

    if genre_ids:
        url_params.append(f"with_genres={','.join(genre_ids)}")

    if type == "anime":
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
            results = _parse_tmdb_results(raw_results, type, genre_map)

        if results:
            _cache_set(cache_key, results)
        return TMDBSearchResponse(results=results)

    except Exception as e:
        logger.error(f"Discover fetch error: {e}")
        return TMDBSearchResponse(results=[])


@router.get("/details")
async def get_media_details(
    tmdb_id: int = Query(...),
    type: str = Query("movie", pattern="^(movie|tv|anime)$"),
):
    """Fetch complete details, credits, and trailer for a movie/TV show from TMDB."""
    if not settings.TMDB_API_KEY:
        return {}

    tmdb_type = "tv" if type in ("tv", "anime") else "movie"
    client = _get_shared_client()
    
    url_details = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}?api_key={settings.TMDB_API_KEY}"
    url_credits = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}/credits?api_key={settings.TMDB_API_KEY}"
    url_videos = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}/videos?api_key={settings.TMDB_API_KEY}"

    try:
        # Fetch details
        r_details = await client.get(url_details)
        if r_details.status_code != 200:
            return {"error": "Failed to fetch details"}
        details = r_details.json()

        # Fetch credits
        r_credits = await client.get(url_credits)
        credits = r_credits.json() if r_credits.status_code == 200 else {"cast": [], "crew": []}

        # Fetch videos
        r_videos = await client.get(url_videos)
        videos = r_videos.json() if r_videos.status_code == 200 else {"results": []}

        # Parse crew
        crew = credits.get("crew", [])
        directors = [c.get("name") for c in crew if c.get("job") == "Director"]
        writers = [c.get("name") for c in crew if c.get("job") in ("Writer", "Screenplay", "Teleplay", "Author")]
        composers = [c.get("name") for c in crew if c.get("job") in ("Original Music Composer", "Music", "Composer")]
        
        # Parse cast
        cast = credits.get("cast", [])
        top_cast = []
        for member in cast[:8]:
            profile_path = member.get("profile_path")
            top_cast.append({
                "name": member.get("name"),
                "character": member.get("character"),
                "profileUrl": f"https://image.tmdb.org/t/p/w185{profile_path}" if profile_path else None
            })

        # Parse trailer video key
        video_results = videos.get("results", [])
        trailer_key = None
        for video in video_results:
            if video.get("site") == "YouTube" and video.get("type") == "Trailer":
                trailer_key = video.get("key")
                break
        
        # If no trailer, fallback to Teaser or any YouTube video
        if not trailer_key and video_results:
            for video in video_results:
                if video.get("site") == "YouTube":
                    trailer_key = video.get("key")
                    break

        # Map details fields
        title = details.get("title") if tmdb_type == "movie" else details.get("name")
        date_str = details.get("release_date") if tmdb_type == "movie" else details.get("first_air_date")
        year = int(date_str[:4]) if date_str and len(date_str) >= 4 else None
        
        genres = [g.get("name") for g in details.get("genres", [])]
        poster_path = details.get("poster_path")
        backdrop_path = details.get("backdrop_path")

        # Runtime extraction
        runtime = details.get("runtime")
        if not runtime and tmdb_type == "tv":
            run_times = details.get("episode_run_time") or []
            runtime = run_times[0] if run_times else None

        return {
            "tmdbId": tmdb_id,
            "title": title,
            "year": year,
            "overview": details.get("overview"),
            "genres": genres,
            "voteAverage": details.get("vote_average"),
            "voteCount": details.get("vote_count"),
            "posterUrl": f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None,
            "backdropUrl": f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else None,
            "runtime": runtime,
            "directors": directors,
            "writers": writers,
            "composers": composers,
            "cast": top_cast,
            "trailerKey": trailer_key
        }

    except Exception as e:
        logger.error(f"Error fetching details for tmdb_id={tmdb_id}: {e}")
        return {"error": str(e)}


