"""add_requires_gm_approval_to_expenses

Revision ID: 1aff0e1ba2c3
Revises: 29894db8f56f
Create Date: 2026-07-27 10:38:12.424152

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1aff0e1ba2c3'
down_revision: Union[str, None] = '29894db8f56f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('requires_gm_approval', sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.drop_column('requires_gm_approval')
