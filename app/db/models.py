import uuid
from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey, select, UniqueConstraint, ARRAY
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.sql import func
from app.db.database import Base
from typing import List

class User(Base):
    __tablename__ = "User"

    id = Column(String, primary_key=True, index=True) # Assuming CUID string
    name = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    emailVerified = Column(DateTime(timezone=True), nullable=True)
    image = Column(String, nullable=True)
    password = Column(String, nullable=True) # hashed via bcryptjs

    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    watchlist_items = relationship("WatchlistItem", back_populates="user", cascade="all, delete-orphan")

class WatchlistItem(Base):
    __tablename__ = "WatchlistItem"

    id = Column(String, primary_key=True, index=True) # CUID string
    userId = Column(String, ForeignKey("User.id", ondelete="CASCADE"), index=True, nullable=False)

    title = Column(String, nullable=False)
    mediaType = Column(String, nullable=False) # "movie" | "tv" | "anime"
    status = Column(String, nullable=False) # "watched" | "pending" | "wishlist"
    favorite = Column(Boolean, default=False)

    genres = Column(ARRAY(String), default=list)
    notes = Column(String, nullable=True)

    year = Column(Integer, nullable=True)
    endYear = Column(Integer, nullable=True)
    running = Column(Boolean, default=False)

    coverUrl = Column(String, nullable=True)
    runtime = Column(Integer, nullable=True) # total runtime in minutes

    user = relationship("User", back_populates="watchlist_items")

    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('userId', 'title', 'mediaType', name='watchlistitem_userid_title_mediatype_key'),
    )


class MediaCache(Base):
    """Global media metadata cache — shared across all users.
    
    Keyed by (title, mediaType, year). Once a title's metadata is fetched
    from an external API, it's stored here so no subsequent request for the
    same title ever hits the external API again.
    """
    __tablename__ = "MediaCache"

    id        = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title     = Column(String, nullable=False)
    mediaType = Column(String, nullable=False)  # "movie" | "tv" | "anime"
    year      = Column(Integer, nullable=True)

    # Cached metadata from external APIs
    coverUrl  = Column(String, nullable=True)
    genres    = Column(ARRAY(String), default=list)
    runtime   = Column(Integer, nullable=True)     # total runtime in minutes
    tmdbId    = Column(Integer, nullable=True)      # TMDB ID for future deep-links

    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('title', 'mediaType', 'year', name='mediacache_title_type_year_key'),
    )
