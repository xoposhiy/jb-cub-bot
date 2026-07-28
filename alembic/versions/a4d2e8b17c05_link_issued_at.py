"""record when an invite was issued

The invite token no longer carries its own timestamp: it is a short opaque
string, because Telegram's ?start= parameter allows only A-Z a-z 0-9 _ - and
64 characters. The expiry moves to the row instead.

Revision ID: a4d2e8b17c05
Revises: f3c1a9b47e21
Create Date: 2026-07-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a4d2e8b17c05'
down_revision: Union[str, Sequence[str], None] = 'f3c1a9b47e21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('link_issued_at', sa.BigInteger(),
                                     nullable=True))
    # Any invite issued in the old format could never be opened anyway, and its
    # nonce has no issue time — clear them so they read as spent, not pending.
    op.execute("UPDATE users SET link_nonce = NULL")


def downgrade() -> None:
    op.drop_column('users', 'link_issued_at')
