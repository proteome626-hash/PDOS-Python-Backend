"""add doctor visit fields + merge heads

Revision ID: 7f6e5d4c3b2a
Revises: ('f0e1d2c3b4a5', 'd0b3c1e4a5f6', 'b2c3d4e5f6a7')
Create Date: 2026-08-26

- Merges the three existing alembic branch heads.
- clients: gender, rating (1-5), treatment_quality
- visits: visit_type ('center' | 'doctor'), visit_reason, interested_product_ids (JSON)
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '7f6e5d4c3b2a'
down_revision = ('f0e1d2c3b4a5', 'd0b3c1e4a5f6', 'b2c3d4e5f6a7')
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('clients', sa.Column('gender', sa.String(length=10), nullable=True))
    op.add_column('clients', sa.Column('rating', sa.Integer(), nullable=True))
    op.add_column('clients', sa.Column('treatment_quality', sa.String(length=20), nullable=True))

    op.add_column('visits', sa.Column('visit_type', sa.String(length=20),
                                      nullable=False, server_default='center'))
    op.add_column('visits', sa.Column('visit_reason', sa.Text(), nullable=True))
    op.add_column('visits', sa.Column('interested_product_ids', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('visits', 'interested_product_ids')
    op.drop_column('visits', 'visit_reason')
    op.drop_column('visits', 'visit_type')
    op.drop_column('clients', 'treatment_quality')
    op.drop_column('clients', 'rating')
    op.drop_column('clients', 'gender')
