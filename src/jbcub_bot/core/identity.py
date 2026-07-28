from sqlalchemy import select

from jbcub_bot.core.models import Role, User


def find_by_telegram_id(session, telegram_id: int) -> User | None:
    return session.scalar(select(User).where(User.telegram_id == telegram_id))


def find_by_matriculation(session, matriculation: str) -> User | None:
    return session.scalar(
        select(User).where(User.matriculation == matriculation)
    )


def find_impersonation_target(session, ref: str) -> User | None:
    user = session.scalar(select(User).where(User.matriculation == ref))
    if user is not None:
        return user
    if ref.isdigit():
        return session.scalar(select(User).where(User.telegram_id == int(ref)))
    return None


def try_claim_by_handle(session, telegram_id: int, username: str | None) -> User | None:
    if not username:
        return None
    # A departed row is not claimable: binding it would write a telegram_id on
    # every message from someone the bot is going to refuse anyway.
    matches = session.scalars(
        select(User).where(
            User.handle_sheet == username, User.telegram_id.is_(None),
            User.departed_at.is_(None),
        )
    ).all()
    if len(matches) != 1:
        return None  # no unique unclaimed record
    user = matches[0]
    user.telegram_id = telegram_id
    user.handle_observed = username
    session.commit()
    return user


def resolve(session, telegram_id: int, username: str | None) -> User | None:
    user = find_by_telegram_id(session, telegram_id)
    if user is not None:
        if username and user.handle_observed != username:
            user.handle_observed = username
            session.commit()
        return user
    return try_claim_by_handle(session, telegram_id, username)


def reset_binding(session, matriculation: str) -> bool:
    user = find_by_matriculation(session, matriculation)
    if user is None:
        return False
    user.telegram_id = None
    session.commit()
    return True


def apply_bootstrap(principal, telegram_id, username, bootstrap_ids):
    if telegram_id not in bootstrap_ids:
        return principal
    if principal is None:
        # Transient (unsaved) admin so /sync works on an empty DB.
        return User(
            role=Role.ADMIN,
            last_name="(bootstrap admin)",
            telegram_id=telegram_id,
            handle_observed=username,
        )
    principal.role = Role.ADMIN
    return principal


def bind_by_token(session, telegram_id: int, username: str | None, user: User) -> User:
    user.telegram_id = telegram_id
    if username:
        user.handle_observed = username
    user.link_nonce = None  # single-use
    user.link_issued_at = None
    session.commit()
    return user
