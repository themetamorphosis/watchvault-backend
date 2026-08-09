"""add_token_family_to_user

Revision ID: 7f38fb955017
Revises: 
Create Date: 2026-06-19 09:46:14.625191

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f38fb955017'
down_revision: Union[str, Sequence[str], None] = '0001_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('User', sa.Column('token_family', sa.String(), nullable=True))
    op.create_index(op.f('ix_User_token_family'), 'User', ['token_family'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_User_token_family'), table_name='User')
    op.drop_column('User', 'token_family')
