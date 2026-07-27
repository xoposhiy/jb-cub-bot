from sqlalchemy import select

from jbcub_bot.core.models import User
from jbcub_bot.features.directory import matching


def name_tokens(user: User) -> list[str]:
    """Every word a search could reasonably be aiming at."""
    words = f"{user.first_name or ''} {user.last_name or ''}".split()
    return words + [h for h in (user.handle_sheet, user.handle_observed) if h]


def rank_users(session, query: str) -> list[tuple[float, User]]:
    """Everyone matching `query` well enough, best first.

    The whole roster is scored in Python. It is a few dozen rows, and no SQL
    dialect can compare a Cyrillic query against a Latin name anyway.
    """
    if len(matching.fold(query)) < matching.MIN_QUERY_LEN:
        return []
    hits = [(score, user)
            for user in session.scalars(select(User)).all()
            if (score := matching.score(query, name_tokens(user)))
            >= matching.ACCEPT]
    hits.sort(key=lambda hit: (-hit[0], hit[1].full_name))
    return hits


def list_cohort(session, primary_cohort: str) -> list[User]:
    stmt = select(User).where(User.primary_cohort == primary_cohort)
    return list(session.scalars(stmt).all())
