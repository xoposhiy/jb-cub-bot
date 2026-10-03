from sqlalchemy import select

from jbcub_bot.core.models import User
from jbcub_bot.features.directory import matching
from jbcub_bot.features.directory.visibility import cohorts_of


def name_tokens(user: User) -> list[str]:
    """Every word a search could reasonably be aiming at."""
    words = f"{user.first_name or ''} {user.last_name or ''}".split()
    return words + [h for h in (user.handle_sheet, user.handle_observed) if h]


def _visible(stmt, include_departed: bool):
    """Hide the people the roster stopped naming unless the caller asked for them.

    An opt-in parameter rather than a global filter: a caller that wants them
    has to say so at the call site, where whether the viewer is an admin is
    known -- and a new caller that forgets gets the safe answer.
    """
    if include_departed:
        return stmt
    return stmt.where(User.departed_at.is_(None))


def rank_users(session, query: str, *,
               include_departed: bool = False) -> list[tuple[float, User]]:
    """Everyone matching `query` well enough, best first.

    The whole roster is scored in Python. It is a few dozen rows, and no SQL
    dialect can compare a Cyrillic query against a Latin name anyway.
    """
    if len(matching.fold(query)) < matching.MIN_QUERY_LEN:
        return []
    stmt = _visible(select(User), include_departed)
    hits = [(score, user)
            for user in session.scalars(stmt).all()
            if (score := matching.score(query, name_tokens(user)))
            >= matching.ACCEPT]
    hits.sort(key=lambda hit: (-hit[0], hit[1].full_name))
    return hits


def classify(ranked: list[tuple[float, User]]) -> tuple[User | None, list[User]]:
    """A clear leader to show as a profile, or a shortlist close enough to name.

    One rule, so a name-shaped query is judged the same way wherever it is
    asked -- by the deterministic search on the chain and by the agent's own
    `search_people` tool.

    A departed row does not stand in an active row's way: when exactly one row
    among the leaders is active, it leads, and the departed leaders come back
    beside it. Those are usually the same person under an older key, and an
    admin must still be able to reach them.
    """
    if not ranked:
        return None, []
    best, target = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if best - runner_up >= matching.LEAD:
        return target, []
    leaders = [user for score, user in ranked if best - score < matching.LEAD]
    active = [user for user in leaders if not user.departed_at]
    if len(active) == 1:
        return active[0], [user for user in leaders if user.departed_at]
    return None, [user for score, user in ranked if best - score <= matching.SPREAD]


def list_cohort(session, cohort: str, *,
                include_departed: bool = False) -> list[User]:
    """Everyone the cohort's roster names, past cohorts included.

    Filtered in Python: `past_cohorts` is a JSON list, and the roster is small.
    """
    stmt = _visible(select(User), include_departed)
    return [user for user in session.scalars(stmt).all()
            if cohort in cohorts_of(user)]


def list_cohort_names(session) -> list[str]:
    """Every cohort that still has a current member, newest first.

    No `include_departed`: a cohort whose last member left is not a cohort to
    offer. The names are years, so reverse-alphabetical is chronological
    without parsing one.
    """
    stmt = select(User.primary_cohort).where(
        User.primary_cohort.is_not(None), User.departed_at.is_(None)
    ).distinct()
    return sorted(session.scalars(stmt).all(), reverse=True)
