import uuid
from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.sql import func
from app.db.database import Base

class User(Base):
    __tablename__ = "User"

    id = Column(String, primary_key=True, index=True) # Assuming CUID string
    name = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    emailVerified = Column(DateTime(timezone=True), nullable=True)
    image = Column(String, nullable=True)
    password = Column(String, nullable=True) # hashed via bcryptjs
    token_family = Column(String, index=True, nullable=True)

    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    watchlist_items = relationship("WatchlistItem", back_populates="user", cascade="all, delete-orphan")
    share_links = relationship("ShareLink", back_populates="user", cascade="all, delete-orphan")

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
    description = Column(String, nullable=True)

    year = Column(Integer, nullable=True)
    endYear = Column(Integer, nullable=True)
    running = Column(Boolean, default=False)

    coverUrl = Column(String, nullable=True)
    runtime = Column(Integer, nullable=True) # total runtime in minutes

    user = relationship("User", back_populates="watchlist_items")

    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('userId', 'title', 'mediaType', name='watchlistitem_userid_title_mediatype_key'),
        Index('ix_watchlistitem_status', 'status'),
        Index('ix_watchlistitem_mediatype', 'mediaType'),
        # Matches the ORDER BY in GET /watchlist, which reads one user's rows
        # newest-first. Without it that sort is unindexed and every page load
        # sorts the user's whole library.
        Index('ix_watchlistitem_userid_updatedat', 'userId', updatedAt.desc()),
    )


class ShareLink(Base):
    """A public, read-only view of one user's watchlist.

    The slug is chosen by the user and is therefore guessable — anyone who
    tries a plausible handle can find the link. Deleting the row is the
    revocation mechanism; there is no disabled state.

    `statuses` and `mediaTypes` are filters, and an empty array means "no
    filter" rather than "nothing". Storing an explicit empty list keeps the
    NULL-vs-empty ambiguity out of the query in share.py.
    """
    __tablename__ = "ShareLink"

    id = Column(String, primary_key=True, index=True)  # UUID4 string
    userId = Column(String, ForeignKey("User.id", ondelete="CASCADE"), index=True, nullable=False)

    # Stored lowercased so uniqueness is effectively case-insensitive; the
    # lookup in the public endpoint lowercases too.
    slug = Column(String, unique=True, index=True, nullable=False)
    label = Column(String, nullable=True)

    statuses = Column(ARRAY(String), default=list)    # subset of watched/pending/wishlist
    mediaTypes = Column(ARRAY(String), default=list)  # subset of movie/tv/anime
    favoritesOnly = Column(Boolean, default=False)

    user = relationship("User", back_populates="share_links")

    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


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
    description = Column(String, nullable=True)
    runtime   = Column(Integer, nullable=True)     # total runtime in minutes
    tmdbId    = Column(Integer, nullable=True)      # TMDB ID for future deep-links

    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('title', 'mediaType', 'year', name='mediacache_title_type_year_key'),
    )
