"""add_target_notes

Revision ID: efb5c8d284bc
Revises: 0c8d0c1a70e2
Create Date: 2026-07-08 11:20:08.774554

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'efb5c8d284bc'
down_revision: Union[str, None] = '0c8d0c1a70e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('targets', sa.Column('notes', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('targets', 'notes')
