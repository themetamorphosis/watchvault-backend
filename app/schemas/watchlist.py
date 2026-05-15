from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime

MediaType = Literal["movie", "tv", "anime"]
StatusType = Literal["watching", "completed", "on_hold", "dropped", "plan_to_watch"]


class WatchlistItemBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    mediaType: MediaType
    status: StatusType
    favorite: Optional[bool] = False
    genres: Optional[List[str]] = []
    notes: Optional[str] = Field(None, max_length=1000)
    description: Optional[str] = Field(None, max_length=2000)
    year: Optional[int] = None
    endYear: Optional[int] = None
    running: Optional[bool] = False
    coverUrl: Optional[str] = None
    runtime: Optional[int] = None


class WatchlistItemCreate(WatchlistItemBase):
    pass


class WatchlistItemUpdate(BaseModel):
    status: Optional[StatusType] = None
    favorite: Optional[bool] = None
    genres: Optional[List[str]] = None
    notes: Optional[str] = Field(None, max_length=1000)
    description: Optional[str] = Field(None, max_length=2000)
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
