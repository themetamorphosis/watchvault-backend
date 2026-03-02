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
    async with httpx.AsyncClient(timeout=10.0) as client:
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
        
        # strip html tags from summary
        import re
        raw_summary = show.get("summary") or ""
        clean_summary = re.sub('<[^<]+?>', '', raw_summary) if raw_summary else None

        return {
            "coverUrl": images.get("original") or images.get("medium"),
            "genres": show.get("genres", []),
            "description": clean_summary
        }

async def _fetch_jikan_poster(title: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
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
            "description": anime.get("synopsis")
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
    async with httpx.AsyncClient(timeout=10.0) as client:
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
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
        if r.status_code != 200: return None
        results = r.json().get("results", [])
        if not results: return None
        movie_id = results[0].get("id")
        d_req = await client.get(f"https://api.themoviedb.org/3/movie/{movie_id}?api_key={settings.TMDB_API_KEY}")
        if d_req.status_code != 200: return None
        return d_req.json().get("runtime")

async def _fetch_tv_runtime(title: str, year: Optional[int]) -> Optional[int]:
    if not settings.TMDB_API_KEY:
        return None
    url = f"https://api.themoviedb.org/3/search/tv?api_key={settings.TMDB_API_KEY}&query={title}&include_adult=false&page=1"
    if year:
        url += f"&first_air_date_year={year}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
        if r.status_code != 200: return None
        results = r.json().get("results", [])
        if not results: return None
        show_id = results[0].get("id")
        d_req = await client.get(f"https://api.themoviedb.org/3/tv/{show_id}?api_key={settings.TMDB_API_KEY}")
        if d_req.status_code != 200: return None
        info = d_req.json()
        ep_runtimes = info.get("episode_run_time") or []
        last_episode = info.get("last_episode_to_air") or {}
        ep_runtime = sum(ep_runtimes) / len(ep_runtimes) if ep_runtimes else (last_episode.get("runtime") or 25)
        total_eps = info.get("number_of_episodes") or 1
        return int(round(ep_runtime * total_eps))

async def _fetch_anime_runtime(title: str) -> Optional[int]:
    import re
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"https://api.jikan.moe/v4/anime?q={title}&limit=1")
        if r.status_code != 200: return None
        data = r.json().get("data", [])
        if not data: return None
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

# ── Persistent HTTP client (reuses TCP connections) ──────────
_tmdb_client: httpx.AsyncClient | None = None

def _get_tmdb_client() -> httpx.AsyncClient:
    global _tmdb_client
    if _tmdb_client is None or _tmdb_client.is_closed:
        _tmdb_client = httpx.AsyncClient(
            timeout=4.0,
            http2=False,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _tmdb_client

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
    type: str = Query("movie", regex="^(movie|tv|anime)$"),
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
        client = _get_tmdb_client()
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
            genres = [genre_map[g] for g in item.get("genre_ids", []) if g in genre_map]

            results.append(TMDBSearchResult(
                tmdbId=item.get("id", 0),
                title=title,
                year=year,
                posterUrl=f"https://image.tmdb.org/t/p/w185{poster_path}" if poster_path else None,
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


