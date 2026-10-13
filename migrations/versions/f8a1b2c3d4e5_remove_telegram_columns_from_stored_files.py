"""remove_telegram_columns_from_stored_files

Removes the three Telegram-specific columns that were added in migration
29894db8f56f. Storage is now local-only (uploads/ directory); no external
Telegram backend is used.

Revision ID: f8a1b2c3d4e5
Revises: 87ce2b8ac108
Create Date: 2026-08-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8a1b2c3d4e5'
down_revision: Union[str, None] = '87ce2b8ac108'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop Telegram columns — local_path remains the sole file reference.
    with op.batch_alter_table('stored_files') as batch_op:
        batch_op.drop_column('telegram_file_id')
        batch_op.drop_column('telegram_unique_id')
        batch_op.drop_column('telegram_file_path')


def downgrade() -> None:
    # Re-add columns for rollback (all nullable so existing rows are unaffected).
    with op.batch_alter_table('stored_files') as batch_op:
        batch_op.add_column(sa.Column('telegram_file_path', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('telegram_unique_id', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('telegram_file_id', sa.String(), nullable=True))
