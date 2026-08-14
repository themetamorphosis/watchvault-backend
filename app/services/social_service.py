"""Shared predicates for the friend graph and direct messages.

A `Friendship` row is stored once per pair, keeping whoever sent the request as
`requesterId` forever. Every read therefore has to consider both orderings, and
getting that wrong in one place is how an authorization check silently passes.
So the predicate lives here and nowhere else.
"""
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"


def friendship_pair_clause(user_a_id: str, user_b_id: str):
    """Matches the single row for this pair, whichever way round it was made."""
    return or_(
        and_(
            models.Friendship.requesterId == user_a_id,
            models.Friendship.addresseeId == user_b_id,
        ),
        and_(
            models.Friendship.requesterId == user_b_id,
            models.Friendship.addresseeId == user_a_id,
        ),
    )


async def get_friendship(
    db: AsyncSession, user_a_id: str, user_b_id: str
) -> Optional[models.Friendship]:
    result = await db.execute(
        select(models.Friendship).where(friendship_pair_clause(user_a_id, user_b_id))
    )
    return result.scalars().first()


async def are_friends(db: AsyncSession, user_a_id: str, user_b_id: str) -> bool:
    """True only for an accepted friendship.

    A pending request is not a friendship: it must not let either side read or
    write the conversation.
    """
    friendship = await get_friendship(db, user_a_id, user_b_id)
    return friendship is not None and friendship.status == STATUS_ACCEPTED


def conversation_clause(user_a_id: str, user_b_id: str):
    """Matches every message between two users, in either direction."""
    return or_(
        and_(
            models.Message.senderId == user_a_id,
            models.Message.recipientId == user_b_id,
        ),
        and_(
            models.Message.senderId == user_b_id,
            models.Message.recipientId == user_a_id,
        ),
    )
