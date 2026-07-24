from sqlalchemy import or_, select

from jbcub_bot.core.models import User


def search_users(session, query: str) -> list[User]:
    pattern = f"%{query.lower()}%"
    stmt = select(User).where(
        or_(
            User.last_name.ilike(pattern),
            User.first_name.ilike(pattern),
            User.handle_sheet.ilike(pattern),
            User.handle_observed.ilike(pattern),
        )
    )
    return list(session.scalars(stmt).all())


def list_cohort(session, primary_cohort: str) -> list[User]:
    stmt = select(User).where(User.primary_cohort == primary_cohort)
    return list(session.scalars(stmt).all())
