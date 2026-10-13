"""add regions and user regions

Revision ID: b1a2c3d4e5f6
Revises: f8a1b2c3d4e5
Create Date: 2026-10-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import uuid

# revision identifiers, used by Alembic.
revision: str = 'b1a2c3d4e5f6'
down_revision: Union[str, None] = 'f8a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_REGIONS = [
    {"name_ar": "أربيل", "name_en": "Erbil"},
    {"name_ar": "دهوك", "name_en": "Duhok"},
    {"name_ar": "بغداد", "name_en": "Baghdad"},
    {"name_ar": "كركوك", "name_en": "Kirkuk"},
    {"name_ar": "السليمانية", "name_en": "Sulaymaniyah"},
    {"name_ar": "البصرة", "name_en": "Basra"},
    {"name_ar": "نينوى", "name_en": "Nineveh"},
]

def upgrade() -> None:
    # 1. Create regions table
    op.create_table(
        'regions',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('name_ar', sa.String(), nullable=False),
        sa.Column('name_en', sa.String(), nullable=False),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # 2. Create user_regions table
    op.create_table(
        'user_regions',
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('region_id', sa.String(), sa.ForeignKey('regions.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # 3. Add region_id to existing tables
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('region_id', sa.String(), sa.ForeignKey('regions.id'), nullable=True))

    with op.batch_alter_table('centers') as batch_op:
        batch_op.add_column(sa.Column('region_id', sa.String(), sa.ForeignKey('regions.id'), nullable=True))

    with op.batch_alter_table('clients') as batch_op:
        batch_op.add_column(sa.Column('region_id', sa.String(), sa.ForeignKey('regions.id'), nullable=True))

    with op.batch_alter_table('visits') as batch_op:
        batch_op.add_column(sa.Column('region_id', sa.String(), sa.ForeignKey('regions.id'), nullable=True))

    with op.batch_alter_table('tasks') as batch_op:
        batch_op.add_column(sa.Column('region_id', sa.String(), sa.ForeignKey('regions.id'), nullable=True))

    # 4. Seed initial regions and backfill existing text values
    conn = op.get_bind()
    regions_map = {}
    for r in DEFAULT_REGIONS:
        rid = str(uuid.uuid4())
        conn.execute(
            sa.text("INSERT INTO regions (id, name_ar, name_en, is_active) VALUES (:id, :ar, :en, 1)"),
            {"id": rid, "ar": r["name_ar"], "en": r["name_en"]}
        )
        regions_map[r["name_en"].lower()] = rid
        regions_map[r["name_ar"]] = rid

    # Backfill users
    for key, rid in regions_map.items():
        conn.execute(
            sa.text("UPDATE users SET region_id = :rid WHERE region_id IS NULL AND (LOWER(region) = :key OR region = :key)"),
            {"rid": rid, "key": key}
        )
        conn.execute(
            sa.text("UPDATE centers SET region_id = :rid WHERE region_id IS NULL AND (LOWER(region) = :key OR region = :key)"),
            {"rid": rid, "key": key}
        )
        conn.execute(
            sa.text("UPDATE clients SET region_id = :rid WHERE region_id IS NULL AND (LOWER(region) = :key OR region = :key)"),
            {"rid": rid, "key": key}
        )

    # Backfill visits from rep's region_id or client/center region_id
    conn.execute(sa.text("""
        UPDATE visits SET region_id = (
            SELECT region_id FROM users WHERE users.id = visits.rep_id
        ) WHERE region_id IS NULL
    """))

    # Backfill tasks from rep's region_id
    conn.execute(sa.text("""
        UPDATE tasks SET region_id = (
            SELECT region_id FROM users WHERE users.id = tasks.rep_id
        ) WHERE region_id IS NULL
    """))


def downgrade() -> None:
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.drop_column('region_id')
    with op.batch_alter_table('visits') as batch_op:
        batch_op.drop_column('region_id')
    with op.batch_alter_table('clients') as batch_op:
        batch_op.drop_column('region_id')
    with op.batch_alter_table('centers') as batch_op:
        batch_op.drop_column('region_id')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('region_id')
    op.drop_table('user_regions')
    op.drop_table('regions')
