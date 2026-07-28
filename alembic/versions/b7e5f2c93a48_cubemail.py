"""store the CUB university email

Both roster tabs have named a `cubemail` column for a while; without a column to
land in, every sync read it and threw it away.

Revision ID: b7e5f2c93a48
Revises: a4d2e8b17c05
Create Date: 2026-07-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b7e5f2c93a48'
down_revision: Union[str, Sequence[str], None] = 'a4d2e8b17c05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('cubemail', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'cubemail')
