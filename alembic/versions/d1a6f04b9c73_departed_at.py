"""mark students the roster no longer names

Revision ID: d1a6f04b9c73
Revises: b7e5f2c93a48
Create Date: 2026-07-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd1a6f04b9c73'
down_revision: Union[str, Sequence[str], None] = 'b7e5f2c93a48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable and unset: everyone already imported counts as active until a
    # /sync reads the rosters and says otherwise.
    op.add_column('users', sa.Column('departed_at', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'departed_at')
