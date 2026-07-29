"""gradebook grades and source_link

Revision ID: e19f6c0a5d3b
Revises: d1a6f04b9c73
Create Date: 2026-07-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e19f6c0a5d3b"
down_revision: Union[str, Sequence[str], None] = "d1a6f04b9c73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("source_link", sa.String(), nullable=True))
    op.create_table(
        "grades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("cohort", sa.String(), nullable=False),
        sa.Column("term", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_grades_user_id"), "grades", ["user_id"], unique=False)
    op.create_index(op.f("ix_grades_cohort"), "grades", ["cohort"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_grades_cohort"), table_name="grades")
    op.drop_index(op.f("ix_grades_user_id"), table_name="grades")
    op.drop_table("grades")
    op.drop_column("users", "source_link")
