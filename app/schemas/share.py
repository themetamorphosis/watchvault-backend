import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.watchlist import MediaType, StatusType

SLUG_MIN_LENGTH = 3
SLUG_MAX_LENGTH = 32

# Lowercase alphanumerics and internal hyphens. Anchored, so a slug can never
# contain a slash, a dot, or anything else that would change what URL it means.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")

# Slugs that would collide with a frontend route or read as official. The
# public page lives at /share/{slug}, so these are reserved defensively rather
# than because any of them currently conflicts.
RESERVED_SLUGS = {
    "about", "admin", "ai", "api", "auth", "dashboard", "discovery", "help",
    "library", "login", "logout", "lumiere", "new", "null", "profile",
    "public", "register", "settings", "share", "social", "static", "support",
    "undefined", "watchlist", "wishlist",
}


def normalize_slug(value: str) -> str:
    """Lowercase and trim. Uniqueness is stored lowercased, so `/share/Noor`
    and `/share/noor` must not be two different links."""
    return value.strip().lower()


def validate_slug(value: str) -> str:
    slug = normalize_slug(value)
    if not (SLUG_MIN_LENGTH <= len(slug) <= SLUG_MAX_LENGTH):
        raise ValueError(
            f"Handle must be between {SLUG_MIN_LENGTH} and {SLUG_MAX_LENGTH} characters"
        )
    if not _SLUG_RE.match(slug):
        raise ValueError(
            "Handle may contain only lowercase letters, numbers and hyphens, "
            "and must start and end with a letter or number"
        )
    if slug in RESERVED_SLUGS:
        raise ValueError(f"'{slug}' is reserved and cannot be used as a handle")
    return slug


class ShareLinkBase(BaseModel):
    label: Optional[str] = Field(None, max_length=80)
    # Empty list means "no filter on this dimension" — see models.ShareLink.
    statuses: List[StatusType] = []
    mediaTypes: List[MediaType] = []
    favoritesOnly: bool = False

    @field_validator("statuses", "mediaTypes")
    @classmethod
    def dedupe(cls, v: List[str]) -> List[str]:
        # Duplicates would widen nothing but would show up in the public
        # payload's filter summary.
        return list(dict.fromkeys(v))


class ShareLinkCreate(ShareLinkBase):
    slug: str

    @field_validator("slug")
    @classmethod
    def check_slug(cls, v: str) -> str:
        return validate_slug(v)


class ShareLinkUpdate(BaseModel):
    """Every field optional: a PATCH that only renames must not clear filters."""
    slug: Optional[str] = None
    label: Optional[str] = Field(None, max_length=80)
    statuses: Optional[List[StatusType]] = None
    mediaTypes: Optional[List[MediaType]] = None
    favoritesOnly: Optional[bool] = None

    @field_validator("slug")
    @classmethod
    def check_slug(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else validate_slug(v)

    @field_validator("statuses", "mediaTypes")
    @classmethod
    def dedupe(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        return None if v is None else list(dict.fromkeys(v))


class ShareLink(ShareLinkBase):
    """`GET/POST/PATCH /share` — the owner's view of their own link."""
    id: str
    slug: str
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

    class Config:
        from_attributes = True


class ShareLinkList(BaseModel):
    links: List[ShareLink]


class PublicOwner(BaseModel):
    """Everything a visitor learns about the owner. Notably not the email."""
    name: Optional[str] = None
    image: Optional[str] = None


class PublicWatchlistItem(BaseModel):
    """A watchlist row stripped for public consumption.

    `notes` and `userId` are absent by design — notes are private annotations,
    and the user id is an internal identifier that would let a visitor
    correlate links. Adding either here leaks it to anonymous visitors.
    """
    id: str
    title: str
    mediaType: MediaType
    status: StatusType
    favorite: bool
    genres: List[str] = []
    description: Optional[str] = None
    year: Optional[int] = None
    endYear: Optional[int] = None
    running: bool = False
    coverUrl: Optional[str] = None
    runtime: Optional[int] = None
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

    class Config:
        from_attributes = True


class PublicShareFilters(BaseModel):
    statuses: List[StatusType] = []
    mediaTypes: List[MediaType] = []
    favoritesOnly: bool = False


class PublicWatchlist(BaseModel):
    """`GET /public/watchlist/{slug}` — served without authentication."""
    slug: str
    label: Optional[str] = None
    owner: PublicOwner
    filters: PublicShareFilters
    items: List[PublicWatchlistItem]
    total: int
    limit: int
    offset: int
    has_more: bool
