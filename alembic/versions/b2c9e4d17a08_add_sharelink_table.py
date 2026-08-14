"""add ShareLink table

Public, no-login watchlist sharing. Each row is one shareable link owned by a
user, with optional status / media-type / favorites filters. Deleting the row
revokes the link, so there is no `enabled` column.

Revision ID: b2c9e4d17a08
Revises: f3a1b36bd24d
Create Date: 2026-08-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b2c9e4d17a08'
down_revision: Union[str, Sequence[str], None] = 'f3a1b36bd24d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ShareLink',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('userId', sa.String(), nullable=False),
        sa.Column('slug', sa.String(), nullable=False),
        sa.Column('label', sa.String(), nullable=True),
        sa.Column('statuses', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('mediaTypes', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('favoritesOnly', sa.Boolean(), nullable=True),
        sa.Column('createdAt', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updatedAt', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['userId'], ['User.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ShareLink_id'), 'ShareLink', ['id'], unique=False)
    op.create_index(op.f('ix_ShareLink_userId'), 'ShareLink', ['userId'], unique=False)
    # Unique because the slug is the entire public address of the link.
    op.create_index(op.f('ix_ShareLink_slug'), 'ShareLink', ['slug'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ShareLink_slug'), table_name='ShareLink')
    op.drop_index(op.f('ix_ShareLink_userId'), table_name='ShareLink')
    op.drop_index(op.f('ix_ShareLink_id'), table_name='ShareLink')
    op.drop_table('ShareLink')
