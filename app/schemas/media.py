from typing import List, Optional
from pydantic import BaseModel

class MediaRequest(BaseModel):
    title: str
    type: str = "movie" # "movie" | "tv" | "anime"
    year: Optional[int] = None

class PosterResponse(BaseModel):
    ok: bool = True
    coverUrl: Optional[str] = None
    genres: List[str] = []

class RuntimeResponse(BaseModel):
    runtime: Optional[int] = None

class MediaCacheResponse(BaseModel):
    """Full cached metadata for a media title."""
    coverUrl: Optional[str] = None
    genres: List[str] = []
    runtime: Optional[int] = None
    cached: bool = False  # True if data came from cache
