"""Add status and rejection_reason to centers

Revision ID: d0b3c1e4a5f6
Revises: ef162eed15d3
Create Date: 2026-07-13 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd0b3c1e4a5f6'
down_revision: Union[str, None] = 'ef162eed15d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('centers', sa.Column('status', sa.String(), server_default='active'))
    op.add_column('centers', sa.Column('rejection_reason', sa.String(), nullable=True))
    op.execute("UPDATE centers SET status = 'active' WHERE status IS NULL")


def downgrade() -> None:
    op.drop_column('centers', 'rejection_reason')
    op.drop_column('centers', 'status')
