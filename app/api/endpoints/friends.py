import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import dependencies
from app.db import models
from app.middleware.rate_limit import limiter
from app.schemas import social as social_schema
from app.services.social_service import (
    STATUS_ACCEPTED,
    STATUS_PENDING,
    get_friendship,
)

router = APIRouter()

SEARCH_MIN_LENGTH = 2
SEARCH_LIMIT = 10


async def _unread_by_sender(db: AsyncSession, user_id: str) -> dict[str, int]:
    """How many unread messages I have from each person, in one query.

    Doing this per friend is what turns a friends list into an N+1.
    """
    result = await db.execute(
        select(models.Message.senderId, func.count())
        .where(
            models.Message.recipientId == user_id,
            models.Message.readAt.is_(None),
        )
        .group_by(models.Message.senderId)
    )
    return {sender_id: count for sender_id, count in result.all()}


@router.get("", response_model=social_schema.FriendsOverview)
async def get_friends(
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    """Friends, requests waiting on me, and requests I'm waiting on."""
    # The other party is whichever side of the row isn't me.
    other_id = case(
        (models.Friendship.requesterId == current_user.id, models.Friendship.addresseeId),
        else_=models.Friendship.requesterId,
    )
    result = await db.execute(
        select(models.Friendship, models.User)
        .join(models.User, models.User.id == other_id)
        .where(
            or_(
                models.Friendship.requesterId == current_user.id,
                models.Friendship.addresseeId == current_user.id,
            )
        )
        .order_by(models.Friendship.createdAt.desc())
    )
    rows = result.all()
    unread = await _unread_by_sender(db, current_user.id)

    friends, incoming, outgoing = [], [], []
    for friendship, other in rows:
        if friendship.status == STATUS_ACCEPTED:
            friends.append(
                {
                    "user": other,
                    "friendshipId": friendship.id,
                    "since": friendship.updatedAt or friendship.createdAt,
                    "unread": unread.get(other.id, 0),
                }
            )
        elif friendship.addresseeId == current_user.id:
            incoming.append(
                {"id": friendship.id, "user": other, "createdAt": friendship.createdAt}
            )
        else:
            outgoing.append(
                {"id": friendship.id, "user": other, "createdAt": friendship.createdAt}
            )

    # Friends sort by unread first so a waiting conversation is at the top.
    friends.sort(key=lambda f: (-f["unread"], (f["user"].handle or "")))
    return {"friends": friends, "incoming": incoming, "outgoing": outgoing}


@router.get("/search", response_model=social_schema.UserSearchResults)
@limiter.limit("30/minute")
async def search_users(
    request: Request,
    q: str = Query(..., min_length=SEARCH_MIN_LENGTH, max_length=31),
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    """Prefix search over handles.

    Handles only — not names or emails. A handle is the one identifier a user
    chose to be findable by, and capping the result count keeps the endpoint
    from being a user directory.
    """
    prefix = q.strip().lstrip("@").lower()
    if len(prefix) < SEARCH_MIN_LENGTH:
        return {"results": []}

    result = await db.execute(
        select(models.User)
        .where(
            models.User.handle.is_not(None),
            models.User.handle.like(f"{_escape_like(prefix)}%", escape="\\"),
        )
        .order_by(models.User.handle)
        .limit(SEARCH_LIMIT)
    )
    users = result.scalars().all()

    # One query for the relationship to every hit, rather than one per hit.
    ids = [u.id for u in users if u.id != current_user.id]
    relationships: dict[str, str] = {}
    if ids:
        rel_rows = await db.execute(
            select(models.Friendship).where(
                or_(
                    (models.Friendship.requesterId == current_user.id)
                    & (models.Friendship.addresseeId.in_(ids)),
                    (models.Friendship.addresseeId == current_user.id)
                    & (models.Friendship.requesterId.in_(ids)),
                )
            )
        )
        for friendship in rel_rows.scalars().all():
            other_id = (
                friendship.addresseeId
                if friendship.requesterId == current_user.id
                else friendship.requesterId
            )
            if friendship.status == STATUS_ACCEPTED:
                relationships[other_id] = "friends"
            elif friendship.addresseeId == current_user.id:
                relationships[other_id] = "incoming"
            else:
                relationships[other_id] = "outgoing"

    return {
        "results": [
            {
                "id": u.id,
                "handle": u.handle,
                "name": u.name,
                "image": u.image,
                "relationship": (
                    "self"
                    if u.id == current_user.id
                    else relationships.get(u.id, "none")
                ),
            }
            for u in users
        ]
    }


def _escape_like(value: str) -> str:
    """Neutralize LIKE wildcards so `%` in a query can't match everything."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.post("/requests", response_model=social_schema.FriendRequestSummary, status_code=201)
@limiter.limit("20/minute")
async def send_friend_request(
    request: Request,
    payload: social_schema.FriendRequestCreate,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    handle = payload.handle.strip().lstrip("@").lower()
    result = await db.execute(select(models.User).where(models.User.handle == handle))
    target = result.scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail=f"No user with the handle @{handle}")
    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot add yourself")

    existing = await get_friendship(db, current_user.id, target.id)
    if existing:
        if existing.status == STATUS_ACCEPTED:
            raise HTTPException(status_code=409, detail=f"You and @{handle} are already friends")
        if existing.requesterId == current_user.id:
            raise HTTPException(status_code=409, detail=f"You already have a request to @{handle}")
        # They requested me and I'm requesting them: that is mutual consent, so
        # accept rather than creating a second row the unique constraint would
        # reject anyway.
        existing.status = STATUS_ACCEPTED
        await db.commit()
        await db.refresh(existing)
        return {"id": existing.id, "user": target, "createdAt": existing.createdAt}

    friendship = models.Friendship(
        id=str(uuid.uuid4()),
        requesterId=current_user.id,
        addresseeId=target.id,
        status=STATUS_PENDING,
    )
    db.add(friendship)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A request already exists")
    await db.refresh(friendship)
    return {"id": friendship.id, "user": target, "createdAt": friendship.createdAt}


async def _get_pending_for_me(
    friendship_id: str, user_id: str, db: AsyncSession
) -> models.Friendship:
    result = await db.execute(
        select(models.Friendship).where(models.Friendship.id == friendship_id)
    )
    friendship = result.scalars().first()
    # 404 rather than 403 for someone else's request: confirming the id exists
    # would leak that two other people are connected.
    if not friendship or user_id not in (friendship.requesterId, friendship.addresseeId):
        raise HTTPException(status_code=404, detail="Request not found")
    return friendship


@router.post("/requests/{friendship_id}/accept", response_model=social_schema.FriendSummary)
async def accept_friend_request(
    friendship_id: str,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    friendship = await _get_pending_for_me(friendship_id, current_user.id, db)
    if friendship.addresseeId != current_user.id:
        # The requester accepting their own request would be a one-sided
        # friendship, which is the whole thing mutual consent prevents.
        raise HTTPException(status_code=400, detail="You cannot accept a request you sent")
    if friendship.status == STATUS_ACCEPTED:
        raise HTTPException(status_code=409, detail="Already friends")

    friendship.status = STATUS_ACCEPTED
    await db.commit()
    await db.refresh(friendship)

    other = (
        await db.execute(
            select(models.User).where(models.User.id == friendship.requesterId)
        )
    ).scalars().first()
    return {
        "user": other,
        "friendshipId": friendship.id,
        "since": friendship.updatedAt or friendship.createdAt,
        "unread": 0,
    }


@router.delete("/requests/{friendship_id}")
async def decline_friend_request(
    friendship_id: str,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    """Decline a request sent to me, or cancel one I sent.

    Both delete the row: there is no `rejected` state, so the sender sees the
    request disappear without being told it was refused.
    """
    friendship = await _get_pending_for_me(friendship_id, current_user.id, db)
    if friendship.status == STATUS_ACCEPTED:
        raise HTTPException(
            status_code=400, detail="Already friends — remove the friend instead"
        )
    await db.delete(friendship)
    await db.commit()
    return {"ok": True}


@router.delete("/{user_id}")
async def remove_friend(
    user_id: str,
    current_user: models.User = Depends(dependencies.get_current_user),
    db: AsyncSession = Depends(dependencies.get_db),
):
    """Unfriend. Messages are deliberately left alone: deleting the row here
    would erase both people's history because one of them clicked remove."""
    friendship = await get_friendship(db, current_user.id, user_id)
    if not friendship or friendship.status != STATUS_ACCEPTED:
        raise HTTPException(status_code=404, detail="Friend not found")
    await db.delete(friendship)
    await db.commit()
    return {"ok": True}
