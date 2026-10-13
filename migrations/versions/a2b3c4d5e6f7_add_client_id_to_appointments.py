"""add client_id to appointments

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-15 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c['name'] for c in inspector.get_columns('appointments')]
    if 'client_id' in cols:
        return
    with op.batch_alter_table('appointments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('client_id', sa.String(), nullable=True))
        batch_op.create_foreign_key('fk_appointments_clients', 'clients', ['client_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('appointments', schema=None) as batch_op:
        batch_op.drop_constraint('fk_appointments_clients', type_='foreignkey')
        batch_op.drop_column('client_id')
