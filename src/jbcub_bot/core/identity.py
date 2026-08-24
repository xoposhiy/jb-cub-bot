import secrets

from sqlalchemy import select

from jbcub_bot.core.models import Role, User

# A roster row the sheet has not numbered yet is keyed by the bot instead, so
# `matriculation` stays a non-empty unique string everywhere. A constant rather
# than a setting: a value drifting from what the rows hold would turn every
# such person into an ordinary student and lift every restriction at once.
PROVISIONAL_PREFIX = "TMP-"


def new_provisional_key() -> str:
    """A key for a roster row that carries no matriculation number yet.

    Random rather than derived from the person: two students sharing a name
    would merge into one row, and a name collision is something this project
    reports rather than resolves.
    """
    return PROVISIONAL_PREFIX + secrets.token_hex(4).upper()


def is_provisional(user: User | None) -> bool:
    """True while this row is a projection of the sheet, not a student's own.

    Every byte of such a row comes from the sheet, the invented key included,
    and the next `/sync` throws it away and builds it again -- so nothing may
    be written to it and nothing may be keyed on it beyond that sync.

    Case-insensitive: a prefix typed into a sheet by hand must leave the person
    restricted rather than promote them.
    """
    if user is None or not user.matriculation:
        return False
    return user.matriculation.casefold().startswith(
        PROVISIONAL_PREFIX.casefold())


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


def closed_out(user: User | None, bootstrap_ids) -> bool:
    """True when the roster no longer lists this person and nothing exempts them.

    One predicate for both callers, and that is the point: asked of the admin
    before their `/as` mode is honoured, and of whoever ends up the principal.
    So the refusal an admin meets under `/as` is not a check resembling the
    target's; it *is* the target's.

    `BOOTSTRAP_ADMIN_IDS` is the exemption, and it belongs to the person, not
    to whoever sent the update: a bad `/sync` must not lock out the one who can
    fix it, and `/as` on them must show the bot working, because working is
    what they get.
    """
    return (user is not None and bool(user.departed_at)
            and user.telegram_id not in bootstrap_ids)


def try_claim_by_handle(session, telegram_id: int, username: str | None) -> User | None:
    if not username:
        return None
    # A departed row is not claimable: binding it would write a telegram_id for
    # someone the bot is going to refuse anyway.
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
