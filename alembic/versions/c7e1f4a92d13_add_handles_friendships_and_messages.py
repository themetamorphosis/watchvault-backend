"""add handles, friendships and messages

The social section: a public `handle` on User for discovery, `Friendship` for
the mutual-accept friend graph, and `Message` for direct messages with an
optional snapshotted title attachment.

`User.handle` is nullable — existing accounts have none until they claim one,
and the unique index tolerates any number of NULLs in PostgreSQL.

Revision ID: c7e1f4a92d13
Revises: b2c9e4d17a08
Create Date: 2026-08-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e1f4a92d13'
down_revision: Union[str, Sequence[str], None] = 'b2c9e4d17a08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('User', sa.Column('handle', sa.String(), nullable=True))
    op.create_index(op.f('ix_User_handle'), 'User', ['handle'], unique=True)

    op.create_table(
        'Friendship',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('requesterId', sa.String(), nullable=False),
        sa.Column('addresseeId', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('createdAt', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updatedAt', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['requesterId'], ['User.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['addresseeId'], ['User.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('requesterId', 'addresseeId', name='friendship_requester_addressee_key'),
    )
    op.create_index(op.f('ix_Friendship_id'), 'Friendship', ['id'], unique=False)
    op.create_index(op.f('ix_Friendship_requesterId'), 'Friendship', ['requesterId'], unique=False)
    op.create_index(op.f('ix_Friendship_addresseeId'), 'Friendship', ['addresseeId'], unique=False)
    op.create_index('ix_friendship_addressee_status', 'Friendship', ['addresseeId', 'status'], unique=False)
    op.create_index('ix_friendship_requester_status', 'Friendship', ['requesterId', 'status'], unique=False)

    op.create_table(
        'Message',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('senderId', sa.String(), nullable=False),
        sa.Column('recipientId', sa.String(), nullable=False),
        sa.Column('body', sa.String(), nullable=True),
        sa.Column('itemTitle', sa.String(), nullable=True),
        sa.Column('itemMediaType', sa.String(), nullable=True),
        sa.Column('itemYear', sa.Integer(), nullable=True),
        sa.Column('itemCoverUrl', sa.String(), nullable=True),
        sa.Column('readAt', sa.DateTime(timezone=True), nullable=True),
        sa.Column('createdAt', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updatedAt', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['senderId'], ['User.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['recipientId'], ['User.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_Message_id'), 'Message', ['id'], unique=False)
    op.create_index(op.f('ix_Message_senderId'), 'Message', ['senderId'], unique=False)
    op.create_index(op.f('ix_Message_recipientId'), 'Message', ['recipientId'], unique=False)
    op.create_index('ix_message_sender_recipient_created', 'Message', ['senderId', 'recipientId', 'createdAt'], unique=False)
    op.create_index('ix_message_recipient_sender_created', 'Message', ['recipientId', 'senderId', 'createdAt'], unique=False)
    op.create_index('ix_message_recipient_readat', 'Message', ['recipientId', 'readAt'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_message_recipient_readat', table_name='Message')
    op.drop_index('ix_message_recipient_sender_created', table_name='Message')
    op.drop_index('ix_message_sender_recipient_created', table_name='Message')
    op.drop_index(op.f('ix_Message_recipientId'), table_name='Message')
    op.drop_index(op.f('ix_Message_senderId'), table_name='Message')
    op.drop_index(op.f('ix_Message_id'), table_name='Message')
    op.drop_table('Message')

    op.drop_index('ix_friendship_requester_status', table_name='Friendship')
    op.drop_index('ix_friendship_addressee_status', table_name='Friendship')
    op.drop_index(op.f('ix_Friendship_addresseeId'), table_name='Friendship')
    op.drop_index(op.f('ix_Friendship_requesterId'), table_name='Friendship')
    op.drop_index(op.f('ix_Friendship_id'), table_name='Friendship')
    op.drop_table('Friendship')

    op.drop_index(op.f('ix_User_handle'), table_name='User')
    op.drop_column('User', 'handle')
