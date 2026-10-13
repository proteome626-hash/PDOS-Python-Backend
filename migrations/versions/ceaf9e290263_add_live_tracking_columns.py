"""add live tracking columns

Revision ID: ceaf9e290263
Revises: 4229f85937ba
Create Date: 2026-07-07 10:27:58.390618

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ceaf9e290263'
down_revision: Union[str, None] = '4229f85937ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_lat', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('last_lng', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('last_location_update', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('last_location_update')
        batch_op.drop_column('last_lng')
        batch_op.drop_column('last_lat')
