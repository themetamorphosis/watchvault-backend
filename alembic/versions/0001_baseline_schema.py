"""baseline schema: User, WatchlistItem, MediaCache

This is the initial schema for the database. It was added retroactively: the
first migration in this repository (7f38fb955017) assumed a "User" table that
no migration had ever created, so `alembic upgrade head` against an empty
database failed and production could not be provisioned at all.

Deliberately excludes User.token_family — 7f38fb955017 adds that and now runs
directly after this revision.

Existing databases created via `Base.metadata.create_all` should be stamped
rather than upgraded:

    alembic stamp 7f38fb955017

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the initial three-table schema."""
    op.create_table(
        "User",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("emailVerified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("image", sa.String(), nullable=True),
        sa.Column("password", sa.String(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updatedAt", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_User_id"), "User", ["id"], unique=False)
    op.create_index(op.f("ix_User_email"), "User", ["email"], unique=True)

    op.create_table(
        "WatchlistItem",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("mediaType", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("favorite", sa.Boolean(), nullable=True),
        sa.Column("genres", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("endYear", sa.Integer(), nullable=True),
        sa.Column("running", sa.Boolean(), nullable=True),
        sa.Column("coverUrl", sa.String(), nullable=True),
        sa.Column("runtime", sa.Integer(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updatedAt", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "userId", "title", "mediaType", name="watchlistitem_userid_title_mediatype_key"
        ),
    )
    op.create_index(op.f("ix_WatchlistItem_id"), "WatchlistItem", ["id"], unique=False)
    op.create_index(op.f("ix_WatchlistItem_userId"), "WatchlistItem", ["userId"], unique=False)
    op.create_index("ix_watchlistitem_status", "WatchlistItem", ["status"], unique=False)
    op.create_index("ix_watchlistitem_mediatype", "WatchlistItem", ["mediaType"], unique=False)

    op.create_table(
        "MediaCache",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("mediaType", sa.String(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("coverUrl", sa.String(), nullable=True),
        sa.Column("genres", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("runtime", sa.Integer(), nullable=True),
        sa.Column("tmdbId", sa.Integer(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updatedAt", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("title", "mediaType", "year", name="mediacache_title_type_year_key"),
    )


def downgrade() -> None:
    """Drop the initial schema."""
    op.drop_table("MediaCache")

    op.drop_index("ix_watchlistitem_mediatype", table_name="WatchlistItem")
    op.drop_index("ix_watchlistitem_status", table_name="WatchlistItem")
    op.drop_index(op.f("ix_WatchlistItem_userId"), table_name="WatchlistItem")
    op.drop_index(op.f("ix_WatchlistItem_id"), table_name="WatchlistItem")
    op.drop_table("WatchlistItem")

    op.drop_index(op.f("ix_User_email"), table_name="User")
    op.drop_index(op.f("ix_User_id"), table_name="User")
    op.drop_table("User")
