from typing import List, Optional
from pydantic import BaseModel, Field

class MediaRequest(BaseModel):
    title: str
    type: str = "movie" # "movie" | "tv" | "anime"
    year: Optional[int] = None

class PosterResponse(BaseModel):
    ok: bool = True
    coverUrl: Optional[str] = None
    genres: List[str] = []
    description: Optional[str] = None

class RuntimeResponse(BaseModel):
    runtime: Optional[int] = None

class MediaCacheResponse(BaseModel):
    """Full cached metadata for a media title."""
    coverUrl: Optional[str] = None
    genres: List[str] = []
    description: Optional[str] = None
    runtime: Optional[int] = None
    cached: bool = False  # True if data came from cache


class TMDBSearchResult(BaseModel):
    """A single TMDB search result for the autocomplete dropdown."""
    tmdbId: int
    title: str
    year: Optional[int] = None
    posterUrl: Optional[str] = None
    overview: Optional[str] = None
    mediaType: str  # "movie" | "tv"
    genres: List[str] = []
    voteAverage: Optional[float] = None


class TMDBSearchResponse(BaseModel):
    results: List[TMDBSearchResult] = []
