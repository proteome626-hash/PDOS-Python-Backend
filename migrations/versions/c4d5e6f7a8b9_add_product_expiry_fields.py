"""add expiry_date and expiry_alert_days to products

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7, d0b3c1e4a5f6
Create Date: 2026-07-14 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = ('b2c3d4e5f6a7', 'd0b3c1e4a5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('products', sa.Column('expiry_date', sa.DateTime(), nullable=True))
    op.add_column('products', sa.Column('expiry_alert_days', sa.Integer(), server_default='90'))


def downgrade() -> None:
    op.drop_column('products', 'expiry_alert_days')
    op.drop_column('products', 'expiry_date')
