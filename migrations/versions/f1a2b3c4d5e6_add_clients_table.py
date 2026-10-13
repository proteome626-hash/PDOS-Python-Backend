"""add clients table

Revision ID: f1a2b3c4d5e6
Revises: d2d980bb5e7f
Create Date: 2026-07-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'd2d980bb5e7f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'clients' in inspector.get_table_names():
        return
    op.create_table('clients',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('rep_id', sa.String(), nullable=False),
        sa.Column('facility_name', sa.String(), nullable=True),
        sa.Column('facility_type', sa.String(), nullable=True),
        sa.Column('doctor_name', sa.String(), nullable=True),
        sa.Column('specialty', sa.String(), nullable=True),
        sa.Column('birth_date', sa.DateTime(), nullable=True),
        sa.Column('class_tier', sa.String(), nullable=True),
        sa.Column('relationship_type', sa.String(), nullable=True),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('phone_number', sa.String(), nullable=True),
        sa.Column('region', sa.String(), nullable=True),
        sa.Column('area', sa.String(), nullable=True),
        sa.Column('street', sa.String(), nullable=True),
        sa.Column('nearby_landmark', sa.String(), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=True),
        sa.Column('longitude', sa.Float(), nullable=True),
        sa.Column('photo_url', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['rep_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('clients')
