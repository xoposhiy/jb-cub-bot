import enum

from sqlalchemy import JSON, BigInteger, Enum, String
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
    visibility: Mapped[dict] = mapped_column(JSON, default=dict)
    link_nonce: Mapped[str | None] = mapped_column(String)
    link_issued_at: Mapped[int | None] = mapped_column(BigInteger)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()
