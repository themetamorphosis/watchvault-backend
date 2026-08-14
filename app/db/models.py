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
    # Public identity for the social section. Nullable because every account
    # created before handles existed has none until it claims one, and because
    # a user who never opens /social never needs one.
    handle = Column(String, unique=True, index=True, nullable=True)
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


class Friendship(Base):
    """A friend relationship in one of two states.

    One row per pair, always — the requester and addressee keep their original
    roles after acceptance rather than the row being duplicated. Callers must
    therefore check both directions; `friendship_between` in
    `app/services/social_service.py` is the only place that should build that
    predicate.

    Rejecting, cancelling, and unfriending all delete the row. There is no
    `rejected` state to distinguish "declined you" from "hasn't seen it", which
    keeps that information from leaking back to the requester.
    """
    __tablename__ = "Friendship"

    id = Column(String, primary_key=True, index=True)
    requesterId = Column(String, ForeignKey("User.id", ondelete="CASCADE"), index=True, nullable=False)
    addresseeId = Column(String, ForeignKey("User.id", ondelete="CASCADE"), index=True, nullable=False)
    status = Column(String, nullable=False)  # "pending" | "accepted"

    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('requesterId', 'addresseeId', name='friendship_requester_addressee_key'),
        # Supports "everyone who has a relationship with me", which runs on
        # every load of /social in both directions.
        Index('ix_friendship_addressee_status', 'addresseeId', 'status'),
        Index('ix_friendship_requester_status', 'requesterId', 'status'),
    )


class Message(Base):
    """A direct message between two users.

    The attached title is **snapshotted**, not a foreign key to WatchlistItem.
    A recommendation has to survive the sender editing or deleting their own
    copy of the item — otherwise "you should watch this" becomes a blank card
    weeks later, and a delete would rewrite history in someone else's inbox.
    """
    __tablename__ = "Message"

    id = Column(String, primary_key=True, index=True)
    senderId = Column(String, ForeignKey("User.id", ondelete="CASCADE"), index=True, nullable=False)
    recipientId = Column(String, ForeignKey("User.id", ondelete="CASCADE"), index=True, nullable=False)

    body = Column(String, nullable=True)  # max 2000, enforced by Pydantic

    # Attached title. All four are set together or all NULL; `itemTitle` is the
    # field that decides whether an attachment is present.
    itemTitle = Column(String, nullable=True)
    itemMediaType = Column(String, nullable=True)  # "movie" | "tv" | "anime"
    itemYear = Column(Integer, nullable=True)
    itemCoverUrl = Column(String, nullable=True)

    readAt = Column(DateTime(timezone=True), nullable=True)

    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    __table_args__ = (
        # A conversation is read in both directions, so both orderings are
        # indexed: the polling loop runs this query every couple of seconds.
        Index('ix_message_sender_recipient_created', 'senderId', 'recipientId', 'createdAt'),
        Index('ix_message_recipient_sender_created', 'recipientId', 'senderId', 'createdAt'),
        # Unread badge: my inbox, unread only.
        Index('ix_message_recipient_readat', 'recipientId', 'readAt'),
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
