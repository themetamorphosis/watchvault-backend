from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.watchlist import MediaType

MAX_MESSAGE_LENGTH = 2000


class PublicUser(BaseModel):
    """How one user appears to another.

    No email: a friend knows your handle and your name, and nothing that would
    let them log in as you or find you elsewhere.
    """
    id: str
    handle: Optional[str] = None
    name: Optional[str] = None
    image: Optional[str] = None

    class Config:
        from_attributes = True


class FriendSummary(BaseModel):
    """An accepted friend, plus the unread count in that conversation."""
    user: PublicUser
    friendshipId: str
    since: Optional[datetime] = None
    unread: int = 0


class FriendRequestSummary(BaseModel):
    id: str
    user: PublicUser  # The *other* party, whichever direction this points
    createdAt: Optional[datetime] = None


class FriendsOverview(BaseModel):
    """`GET /friends` — everything /social needs for its first paint."""
    friends: List[FriendSummary]
    incoming: List[FriendRequestSummary]
    outgoing: List[FriendRequestSummary]


class FriendRequestCreate(BaseModel):
    handle: str = Field(..., min_length=1, max_length=31)


class UserSearchResult(PublicUser):
    """A search hit, annotated with where you already stand with them.

    Without `relationship` the UI would offer "Add friend" for people who are
    already friends, and the request would fail on submit.
    """
    relationship: Literal["none", "friends", "incoming", "outgoing", "self"]


class UserSearchResults(BaseModel):
    results: List[UserSearchResult]


class MessageAttachment(BaseModel):
    """A title recommended inside a message.

    Snapshotted at send time — see `models.Message`.
    """
    title: str = Field(..., min_length=1, max_length=500)
    mediaType: MediaType
    year: Optional[int] = None
    coverUrl: Optional[str] = None


class MessageCreate(BaseModel):
    body: Optional[str] = Field(None, max_length=MAX_MESSAGE_LENGTH)
    attachment: Optional[MessageAttachment] = None

    @model_validator(mode="after")
    def require_content(self):
        # An empty message is not a message. Whitespace-only counts as empty,
        # otherwise the send button ships a blank bubble.
        has_body = bool(self.body and self.body.strip())
        if not has_body and self.attachment is None:
            raise ValueError("A message needs text, an attachment, or both")
        if has_body:
            self.body = self.body.strip()
        return self


class Message(BaseModel):
    id: str
    senderId: str
    recipientId: str
    body: Optional[str] = None
    attachment: Optional[MessageAttachment] = None
    readAt: Optional[datetime] = None
    createdAt: Optional[datetime] = None


class MessagePage(BaseModel):
    """`GET /messages/{friend_id}` — oldest-first, ready to render top to bottom."""
    messages: List[Message]
    has_more: bool


class ConversationSummary(BaseModel):
    user: PublicUser
    lastMessage: Optional[Message] = None
    unread: int = 0


class ConversationList(BaseModel):
    conversations: List[ConversationSummary]


class UnreadTotal(BaseModel):
    """`GET /messages/unread` — one number for the nav badge."""
    unread: int
