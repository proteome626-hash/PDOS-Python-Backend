"""drop route_points table — privacy decision: no GPS breadcrumb trail

Reason for removal:
  This table was originally created to store a historical trail of GPS
  coordinates (latitude, longitude, recorded_at) collected during a rep's
  workday. This directly contradicts a documented and repeatedly confirmed
  privacy decision: "no historical GPS breadcrumb trail, only current live
  location." The Flutter sync service tried to push these points via
  POST /route-points/batch, but the backend never exposed that endpoint,
  so NO data ever reached the server (all attempts returned 404).

  DO NOT recreate this table or its associated API endpoints without
  explicit documented approval restoring the live-tracking privacy policy.

Revision ID: 7c0af61b59ff
Revises: aa21cfb2e40e
Create Date: 2026-07-22 09:31:12.740580

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c0af61b59ff'
down_revision: Union[str, None] = 'aa21cfb2e40e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('route_points')


def downgrade() -> None:
    op.create_table(
        'route_points',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('rep_id', sa.String(), nullable=False),
        sa.Column('latitude', sa.Float(), nullable=False),
        sa.Column('longitude', sa.Float(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['rep_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
