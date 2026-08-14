import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import dependencies
from app.db import models
from app.middleware.rate_limit import limiter
from app.schemas import social as social_schema
from app.services.social_service import are_friends, conversation_clause

router = APIRouter()

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _serialize(message: models.Message) -> dict:
    """ORM row → API shape, folding the four item columns into one object.

    `itemTitle` is the presence flag: the four are written together or not at
    all, so testing one is enough.
    """
    attachment = None
    if message.itemTitle:
        attachment = {
            "title": message.itemTitle,
            "mediaType": message.itemMediaType,
            "year": message.itemYear,
            "coverUrl": message.itemCoverUrl,
        }
    return {
        "id": message.id,
        "senderId": message.senderId,
        "recipientId": message.recipientId,
        "body": message.body,
        "attachment": attachment,
        "readAt": message.readAt,
        "createdAt": message.createdAt,
    }


async def _require_friend(db: AsyncSession, user_id: str, friend_id: str) -> None:
    """Every read and write in this module goes through here.

    A conversation is readable only by two accepted friends — not by a pending
    request, and not by someone who was unfriended after the fact.
    """
    if not await are_friends(db, user_id, friend_id):
        # 404, not 403: whether a given user exists and has messaged you is
        # itself private.
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.get("/unread", response_model=social_schema.UnreadTotal)
async def get_unread_total(
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    """One number for the nav badge — the cheapest thing the poller can ask."""
    total = (
        await db.execute(
            select(func.count())
            .select_from(models.Message)
            .where(
                models.Message.recipientId == current_user.id,
                models.Message.readAt.is_(None),
            )
        )
    ).scalar()
    return {"unread": total or 0}


@router.get("", response_model=social_schema.ConversationList)
async def list_conversations(
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    """Every accepted friend, with the last message and unread count."""
    other_id = case(
        (models.Friendship.requesterId == current_user.id, models.Friendship.addresseeId),
        else_=models.Friendship.requesterId,
    )
    friend_rows = await db.execute(
        select(models.User)
        .join(models.Friendship, models.User.id == other_id)
        .where(
            models.Friendship.status == "accepted",
            or_(
                models.Friendship.requesterId == current_user.id,
                models.Friendship.addresseeId == current_user.id,
            ),
        )
    )
    friends = friend_rows.scalars().all()
    if not friends:
        return {"conversations": []}

    friend_ids = [f.id for f in friends]

    # All messages exchanged with any friend, newest first. Taking the first
    # row per partner in Python avoids a correlated subquery per friend; a
    # personal tracker's message volume does not justify a window function.
    partner_id = case(
        (models.Message.senderId == current_user.id, models.Message.recipientId),
        else_=models.Message.senderId,
    )
    message_rows = await db.execute(
        select(models.Message, partner_id.label("partner"))
        .where(
            or_(
                (models.Message.senderId == current_user.id)
                & (models.Message.recipientId.in_(friend_ids)),
                (models.Message.recipientId == current_user.id)
                & (models.Message.senderId.in_(friend_ids)),
            )
        )
        .order_by(models.Message.createdAt.desc())
    )

    last_by_partner: dict[str, models.Message] = {}
    unread_by_partner: dict[str, int] = {}
    for message, partner in message_rows.all():
        last_by_partner.setdefault(partner, message)
        if message.recipientId == current_user.id and message.readAt is None:
            unread_by_partner[partner] = unread_by_partner.get(partner, 0) + 1

    conversations = [
        {
            "user": friend,
            "lastMessage": (
                _serialize(last_by_partner[friend.id])
                if friend.id in last_by_partner
                else None
            ),
            "unread": unread_by_partner.get(friend.id, 0),
        }
        for friend in friends
    ]
    # Most recent conversation first; friends you've never messaged sink to the
    # bottom rather than disappearing.
    conversations.sort(
        key=lambda c: (
            c["lastMessage"]["createdAt"] if c["lastMessage"] else datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )
    return {"conversations": conversations}


@router.get("/{friend_id}", response_model=social_schema.MessagePage)
async def get_conversation(
    friend_id: str,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    before: str | None = Query(
        default=None, description="Message id to page backwards from"
    ),
    since: str | None = Query(
        default=None, description="Message id; returns only what arrived after it"
    ),
):
    """Read a conversation, oldest-first.

    Two cursors, because the two callers want opposite things: `before` pages
    backwards through history, `since` is what the poller sends to ask "what's
    new?" and is the reason a 2-second interval stays cheap.
    """
    await _require_friend(db, current_user.id, friend_id)

    base = select(models.Message).where(
        conversation_clause(current_user.id, friend_id)
    )

    if since:
        anchor = await _anchor_time(db, since)
        if anchor is None:
            raise HTTPException(status_code=400, detail="Unknown `since` message")
        rows = (
            await db.execute(
                base.where(models.Message.createdAt > anchor)
                .order_by(models.Message.createdAt.asc())
                .limit(limit)
            )
        ).scalars().all()
        # A poll never reports more history behind it; it only moves forward.
        return {"messages": [_serialize(m) for m in rows], "has_more": False}

    if before:
        anchor = await _anchor_time(db, before)
        if anchor is None:
            raise HTTPException(status_code=400, detail="Unknown `before` message")
        base = base.where(models.Message.createdAt < anchor)

    # Newest `limit` rows, then reversed: the tail of a conversation is what
    # you want on open, but it renders oldest-first.
    rows = (
        await db.execute(
            base.order_by(models.Message.createdAt.desc()).limit(limit + 1)
        )
    ).scalars().all()

    has_more = len(rows) > limit
    page = list(reversed(rows[:limit]))
    return {"messages": [_serialize(m) for m in page], "has_more": has_more}


async def _anchor_time(db: AsyncSession, message_id: str):
    result = await db.execute(
        select(models.Message.createdAt).where(models.Message.id == message_id)
    )
    return result.scalars().first()


@router.post("/{friend_id}", response_model=social_schema.Message, status_code=201)
@limiter.limit("60/minute")
async def send_message(
    request: Request,
    friend_id: str,
    payload: social_schema.MessageCreate,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    await _require_friend(db, current_user.id, friend_id)

    attachment = payload.attachment
    message = models.Message(
        id=str(uuid.uuid4()),
        senderId=current_user.id,
        recipientId=friend_id,
        body=payload.body,
        itemTitle=attachment.title if attachment else None,
        itemMediaType=attachment.mediaType if attachment else None,
        itemYear=attachment.year if attachment else None,
        itemCoverUrl=attachment.coverUrl if attachment else None,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return _serialize(message)


@router.post("/{friend_id}/read")
async def mark_conversation_read(
    friend_id: str,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    """Mark everything they sent me as read.

    One UPDATE over the unread rows rather than a read/unread row per message —
    a conversation is read up to a point, not message by message.
    """
    await _require_friend(db, current_user.id, friend_id)

    result = await db.execute(
        update(models.Message)
        .where(
            models.Message.recipientId == current_user.id,
            models.Message.senderId == friend_id,
            models.Message.readAt.is_(None),
        )
        .values(readAt=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"ok": True, "marked": result.rowcount or 0}
