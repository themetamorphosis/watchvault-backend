"""add userId+updatedAt composite index to WatchlistItem

GET /watchlist filters by userId and sorts by updatedAt DESC. That sort had no
supporting index, so every page load sorted the user's entire library.

Autogenerate also proposed creating ix_watchlistitem_status and
ix_watchlistitem_mediatype here. Both are already created by the baseline
revision (0001_baseline); they only looked missing because the development
database predates them and was stamped rather than upgraded. Recreating them
would abort `alembic upgrade head` on any correctly-migrated database, so they
are deliberately not in this migration.

Revision ID: f3a1b36bd24d
Revises: 7f38fb955017
Create Date: 2026-08-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a1b36bd24d'
down_revision: Union[str, Sequence[str], None] = '7f38fb955017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_watchlistitem_userid_updatedat"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        INDEX_NAME,
        "WatchlistItem",
        ["userId", sa.literal_column('"updatedAt" DESC')],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(INDEX_NAME, table_name="WatchlistItem")
