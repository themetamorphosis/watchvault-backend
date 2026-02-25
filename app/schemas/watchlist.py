from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class WatchlistItemBase(BaseModel):
    title: str
    mediaType: str # "movie" | "tv" | "anime"
    status: str # "watched" | "pending" | "wishlist"
    favorite: Optional[bool] = False
    genres: Optional[List[str]] = []
    notes: Optional[str] = None
    year: Optional[int] = None
    endYear: Optional[int] = None
    running: Optional[bool] = False
    coverUrl: Optional[str] = None
    runtime: Optional[int] = None

class WatchlistItemCreate(WatchlistItemBase):
    pass

class WatchlistItemUpdate(BaseModel):
    status: Optional[str] = None
    favorite: Optional[bool] = None
    genres: Optional[List[str]] = None
    notes: Optional[str] = None
    year: Optional[int] = None
    endYear: Optional[int] = None
    running: Optional[bool] = None
    coverUrl: Optional[str] = None
    runtime: Optional[int] = None

class WatchlistItem(WatchlistItemBase):
    id: str
    userId: str
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

    class Config:
        from_attributes = True
