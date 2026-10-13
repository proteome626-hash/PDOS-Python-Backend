"""add_stored_files_table

Revision ID: 29894db8f56f
Revises: a65f890c5203
Create Date: 2026-07-22 11:23:11.667821

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '29894db8f56f'
down_revision: Union[str, None] = 'a65f890c5203'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('stored_files',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('local_path', sa.String(), nullable=False),
        sa.Column('telegram_file_id', sa.String(), nullable=True),
        sa.Column('telegram_unique_id', sa.String(), nullable=True),
        sa.Column('telegram_file_path', sa.String(), nullable=True),
        sa.Column('original_name', sa.String(), nullable=False),
        sa.Column('mime_type', sa.String(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('local_path'),
    )
    op.add_column('brands', sa.Column('logo_file_id', sa.String(), nullable=True))
    op.create_foreign_key(None, 'brands', 'stored_files', ['logo_file_id'], ['id'])
    op.add_column('clients', sa.Column('photo_file_id', sa.String(), nullable=True))
    op.create_foreign_key(None, 'clients', 'stored_files', ['photo_file_id'], ['id'])
    op.add_column('expenses', sa.Column('receipt_file_id', sa.String(), nullable=True))
    op.create_foreign_key(None, 'expenses', 'stored_files', ['receipt_file_id'], ['id'])
    op.add_column('field_reports', sa.Column('photo_file_id', sa.String(), nullable=True))
    op.create_foreign_key(None, 'field_reports', 'stored_files', ['photo_file_id'], ['id'])
    op.add_column('products', sa.Column('image_file_id', sa.String(), nullable=True))
    op.create_foreign_key(None, 'products', 'stored_files', ['image_file_id'], ['id'])
    op.add_column('users', sa.Column('avatar_file_id', sa.String(), nullable=True))
    op.create_foreign_key(None, 'users', 'stored_files', ['avatar_file_id'], ['id'])
    op.add_column('visit_photos', sa.Column('photo_file_id', sa.String(), nullable=True))
    op.create_foreign_key(None, 'visit_photos', 'stored_files', ['photo_file_id'], ['id'])
    op.add_column('visits', sa.Column('signature_file_id', sa.String(), nullable=True))
    op.create_foreign_key(None, 'visits', 'stored_files', ['signature_file_id'], ['id'])
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_constraint(None, 'visits', type_='foreignkey')
    op.drop_column('visits', 'signature_file_id')
    op.drop_constraint(None, 'visit_photos', type_='foreignkey')
    op.drop_column('visit_photos', 'photo_file_id')
    op.drop_constraint(None, 'users', type_='foreignkey')
    op.drop_column('users', 'avatar_file_id')
    op.drop_constraint(None, 'products', type_='foreignkey')
    op.drop_column('products', 'image_file_id')
    op.drop_constraint(None, 'field_reports', type_='foreignkey')
    op.drop_column('field_reports', 'photo_file_id')
    op.drop_constraint(None, 'expenses', type_='foreignkey')
    op.drop_column('expenses', 'receipt_file_id')
    op.drop_constraint(None, 'clients', type_='foreignkey')
    op.drop_column('clients', 'photo_file_id')
    op.drop_constraint(None, 'brands', type_='foreignkey')
    op.drop_column('brands', 'logo_file_id')
    # ### end Alembic commands ###
