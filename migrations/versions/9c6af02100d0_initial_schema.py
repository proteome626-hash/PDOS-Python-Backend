"""Initial schema

Revision ID: 9c6af02100d0
Revises: 
Create Date: 2026-07-04 15:47:21.294983

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c6af02100d0'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tables are created by Base.metadata.create_all() at application startup.
    # For greenfield PostgreSQL deployment, use scripts/init_prod_db.py
    # which runs create_all() then alembic stamp head.
    pass


def downgrade() -> None:
    pass
