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

from app.schemas.media import PosterResponse, RuntimeResponse
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
        if r.status_code != 200:
            return {"coverUrl": None, "genres": []}
        data = r.json()
        if not data:
            return {"coverUrl": None, "genres": []}
        show = data[0].get("show", {})
        images = show.get("image") or {}
        return {
            "coverUrl": images.get("original") or images.get("medium"),
            "genres": show.get("genres", [])
        }

async def _fetch_jikan_poster(title: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"https://api.jikan.moe/v4/anime?q={title}&limit=1")
        if r.status_code != 200:
            return {"coverUrl": None, "genres": []}
        data = r.json().get("data", [])
        if not data:
            return {"coverUrl": None, "genres": []}
        anime = data[0]
        genres = [g.get("name") for g in anime.get("genres", [])]
        images = anime.get("images", {}).get("jpg", {})
        return {
            "coverUrl": images.get("large_image_url") or images.get("image_url"),
            "genres": genres
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
        ep_runtimes = info.get("episode_run_time", [])
        ep_runtime = sum(ep_runtimes) / len(ep_runtimes) if ep_runtimes else (info.get("last_episode_to_air", {}).get("runtime") or 25)
        total_eps = info.get("number_of_episodes", 1)
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
            tmdb_id=result.get("tmdbId"),
        )
        return result
    except Exception as e:
        logger.warning(f"External API fetch failed for poster '{title}': {e}")
        return {"coverUrl": None, "genres": []}


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
        return PosterResponse(ok=True, coverUrl=cached.coverUrl, genres=cached.genres or [])

    # 2. Cache miss → fetch from external API
    logger.info(f"CACHE MISS (poster): {title} — fetching from external API")
    result = await fetch_and_cache_poster(db, title, type, year)
    return PosterResponse(ok=True, coverUrl=result.get("coverUrl"), genres=result.get("genres", []))


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
