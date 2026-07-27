"""split account fields into roster and self-reported columns

Revision ID: f3c1a9b47e21
Revises: c72c6d99f0c1
Create Date: 2026-07-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f3c1a9b47e21'
down_revision: Union[str, Sequence[str], None] = 'c72c6d99f0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename, don't drop: the roster keeps whatever it already imported."""
    op.alter_column('users', 'github', new_column_name='github_sheet')
    op.alter_column('users', 'codeforces', new_column_name='codeforces_sheet')
    op.add_column('users', sa.Column('github_self', sa.String(), nullable=True))
    op.add_column('users', sa.Column('codeforces_self', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'codeforces_self')
    op.drop_column('users', 'github_self')
    op.alter_column('users', 'codeforces_sheet', new_column_name='codeforces')
    op.alter_column('users', 'github_sheet', new_column_name='github')
