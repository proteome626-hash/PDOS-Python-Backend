"""add_notes_table

Revision ID: f0e1d2c3b4a5
Revises: 1aff0e1ba2c3
Create Date: 2026-08-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f0e1d2c3b4a5'
down_revision: Union[str, None] = '1aff0e1ba2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notes',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('sender_id', sa.String(), nullable=False),
        sa.Column('recipient_id', sa.String(), nullable=False),
        sa.Column('visit_id', sa.String(), nullable=True),
        sa.Column('content', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id'], name='fk_notes_sender_id_users'),
        sa.ForeignKeyConstraint(['recipient_id'], ['users.id'], name='fk_notes_recipient_id_users'),
        sa.ForeignKeyConstraint(['visit_id'], ['visits.id'], name='fk_notes_visit_id_visits'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_notes_recipient_id'), 'notes', ['recipient_id'], unique=False)
    op.create_index(op.f('ix_notes_sender_id'), 'notes', ['sender_id'], unique=False)
    op.create_index(op.f('ix_notes_visit_id'), 'notes', ['visit_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_notes_visit_id'), table_name='notes')
    op.drop_index(op.f('ix_notes_sender_id'), table_name='notes')
    op.drop_index(op.f('ix_notes_recipient_id'), table_name='notes')
    op.drop_table('notes')
