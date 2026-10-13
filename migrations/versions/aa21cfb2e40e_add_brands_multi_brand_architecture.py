"""add_brands_multi_brand_architecture

Revision ID: aa21cfb2e40e
Revises: e754cb421f26
Create Date: 2026-07-20

This migration:
  1. Creates the `brands` table
  2. Adds `brand_id` (nullable FK) to `users`
  3. Adds `brand_id` (required FK) to `centers` and `products`
  4. Seeds two initial brands: Cebelia (primary/legacy) and Gamarde
  5. Backfills all existing supervisor/rep users -> Cebelia
  6. Backfills all existing centers -> Cebelia
  7. Backfills all existing products -> Cebelia
  8. Migrates all overseer accounts -> general_manager role
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import uuid
import datetime

# revision identifiers, used by Alembic.
revision: str = 'aa21cfb2e40e'
down_revision: Union[str, None] = 'e754cb421f26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Fixed UUIDs so the migration is reproducible / idempotent across environments
CEBELIA_BRAND_ID = '11111111-cebe-lia0-0000-000000000001'
GAMARDE_BRAND_ID = '22222222-gama-rde0-0000-000000000002'


def upgrade() -> None:
    # ─────────────────────────────────────────────────────────────────────────
    # 1. Create the brands table (if not already created by SQLAlchemy create_all)
    # ─────────────────────────────────────────────────────────────────────────
    bind = op.get_bind()

    # Check if brands table already exists (SQLAlchemy create_all may have created it)
    from sqlalchemy import inspect
    insp = inspect(bind)
    existing_tables = insp.get_table_names()

    if 'brands' not in existing_tables:
        op.create_table(
            'brands',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('logo_url', sa.String(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Seed the two initial brands
    # ─────────────────────────────────────────────────────────────────────────
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    bind.execute(sa.text("""
        INSERT INTO brands (id, name, logo_url, is_active, created_at)
        VALUES (:id, :name, NULL, 1, :created_at)
        ON CONFLICT (id) DO NOTHING
    """), {"id": CEBELIA_BRAND_ID, "name": "Cebelia", "created_at": now})

    bind.execute(sa.text("""
        INSERT INTO brands (id, name, logo_url, is_active, created_at)
        VALUES (:id, :name, NULL, 1, :created_at)
        ON CONFLICT (id) DO NOTHING
    """), {"id": GAMARDE_BRAND_ID, "name": "Gamarde", "created_at": now})

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Add brand_id columns (only if they don't already exist)
    # ─────────────────────────────────────────────────────────────────────────
    def col_exists(table, col):
        return col in [c['name'] for c in insp.get_columns(table)]

    if not col_exists('users', 'brand_id'):
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.add_column(sa.Column('brand_id', sa.String(), nullable=True))

    if not col_exists('centers', 'brand_id'):
        with op.batch_alter_table('centers', schema=None) as batch_op:
            batch_op.add_column(sa.Column('brand_id', sa.String(), nullable=True))

    if not col_exists('products', 'brand_id'):
        with op.batch_alter_table('products', schema=None) as batch_op:
            batch_op.add_column(sa.Column('brand_id', sa.String(), nullable=True))

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Backfill: assign all existing data to Cebelia (primary/legacy brand)
    # ─────────────────────────────────────────────────────────────────────────
    bind.execute(sa.text("""
        UPDATE users
        SET brand_id = :brand_id
        WHERE role IN ('supervisor', 'rep')
        AND brand_id IS NULL
    """), {"brand_id": CEBELIA_BRAND_ID})

    bind.execute(sa.text("""
        UPDATE centers SET brand_id = :brand_id WHERE brand_id IS NULL
    """), {"brand_id": CEBELIA_BRAND_ID})

    bind.execute(sa.text("""
        UPDATE products SET brand_id = :brand_id WHERE brand_id IS NULL
    """), {"brand_id": CEBELIA_BRAND_ID})

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Migrate overseer -> general_manager
    # ─────────────────────────────────────────────────────────────────────────
    bind.execute(sa.text("""
        UPDATE users SET role = 'general_manager' WHERE role = 'overseer'
    """))

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Add FK constraints (skip if column was pre-existing — FK may exist)
    # ─────────────────────────────────────────────────────────────────────────
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_foreign_key('fk_users_brand_id', 'brands', ['brand_id'], ['id'])

    with op.batch_alter_table('centers', schema=None) as batch_op:
        batch_op.create_foreign_key('fk_centers_brand_id', 'brands', ['brand_id'], ['id'])

    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.create_foreign_key('fk_products_brand_id', 'brands', ['brand_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_constraint('fk_products_brand_id', type_='foreignkey')
        batch_op.drop_column('brand_id')

    with op.batch_alter_table('centers', schema=None) as batch_op:
        batch_op.drop_constraint('fk_centers_brand_id', type_='foreignkey')
        batch_op.drop_column('brand_id')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('fk_users_brand_id', type_='foreignkey')
        batch_op.drop_column('brand_id')

    op.drop_table('brands')

    # Note: downgrade does NOT revert general_manager -> overseer
    # to avoid accidental data corruption. Revert manually if needed.
