"""add_target_views

Revision ID: caf783349d82
Revises: efb5c8d284bc
Create Date: 2026-07-08 11:24:21.425331

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'caf783349d82'
down_revision: Union[str, None] = 'efb5c8d284bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('target_views',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('target_id', sa.String(), nullable=False),
        sa.Column('rep_id', sa.String(), nullable=False),
        sa.Column('viewed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['target_id'], ['targets.id'], ),
        sa.ForeignKeyConstraint(['rep_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('target_views')
