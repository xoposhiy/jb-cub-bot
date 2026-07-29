import enum

from sqlalchemy import JSON, BigInteger, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from jbcub_bot.core.db import Base


class Role(str, enum.Enum):
    ADMIN = "Admin"
    STUDENT = "Student"
    TEACHER = "Teacher"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[Role] = mapped_column(
        Enum(Role, values_callable=lambda e: [m.value for m in e]),
        default=Role.STUDENT,
    )
    last_name: Mapped[str] = mapped_column(String, default="")
    first_name: Mapped[str] = mapped_column(String, default="")
    matriculation: Mapped[str | None] = mapped_column(String, unique=True)
    handle_sheet: Mapped[str | None] = mapped_column(String)
    handle_observed: Mapped[str | None] = mapped_column(String)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    gmail: Mapped[str | None] = mapped_column(String)
    cubemail: Mapped[str | None] = mapped_column(String)
    github_sheet: Mapped[str | None] = mapped_column(String)
    github_self: Mapped[str | None] = mapped_column(String)
    codeforces_sheet: Mapped[str | None] = mapped_column(String)
    codeforces_self: Mapped[str | None] = mapped_column(String)
    birthday: Mapped[str | None] = mapped_column(String)
    citizenship: Mapped[str | None] = mapped_column(String)
    comment: Mapped[str | None] = mapped_column(String)
    status_line: Mapped[str | None] = mapped_column(String)
    primary_cohort: Mapped[str | None] = mapped_column(String, index=True)
    past_cohorts: Mapped[list] = mapped_column(JSON, default=list)
    # The sheet row this profile came from: a Cohorts 'Link' for a cohort
    # student, the Rights spreadsheet's id/URL for a Rights-only row. Set by
    # /sync the way primary_cohort already is.
    source_link: Mapped[str | None] = mapped_column(String)
    visibility: Mapped[dict] = mapped_column(JSON, default=dict)
    # ISO date the roster stopped naming them; NULL means active. A date rather
    # than a flag so an admin can see when the person left.
    departed_at: Mapped[str | None] = mapped_column(String)
    link_nonce: Mapped[str | None] = mapped_column(String)
    link_issued_at: Mapped[int | None] = mapped_column(BigInteger)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Grade(Base):
    """One non-empty cell from a cohort's Gradebook tab.

    ``position`` is the sheet column index and the only ordering: semesters
    sort by the lowest position among their cells, courses by position within
    a semester. Column order is chronological, so no date is parsed.
    """

    __tablename__ = "grades"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    cohort: Mapped[str] = mapped_column(String, index=True)
    term: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, default="")
    label: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(String)
    position: Mapped[int] = mapped_column(Integer)
