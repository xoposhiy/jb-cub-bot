"""Stage 2: whose eyes the update looks through, and who never gets a swap.

These run stages 1, 2 and 4 in the order `main.py` mounts them, because what
is under test is largely the order itself -- that the departed refusal happens
*after* the swap and so belongs to the target, and that an admin the roster
dropped never reaches it through somebody else. A test of the middleware alone
would prove neither. Stage 3, the banner, is a message-only send and is proved
end to end in `tests/test_departed_access.py`.
"""
import functools
from datetime import datetime, timezone

from aiogram.methods import SendMessage
from aiogram.types import Chat, Message
from aiogram.types import User as TgUser

from jbcub_bot.core import impersonation
from jbcub_bot.core.impersonation import ImpersonationMiddleware, TARGET_GONE
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.principal import (
    DEPARTED_NOTICE, AccessMiddleware, PrincipalMiddleware,
)

ADMIN_TID = 777
STUDENT_TID = 111
STUDENT_REF = "30000001"


class FakeBot:
    def __init__(self):
        self.id = 1
        self.sent: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None


def _message(bot, telegram_id: int, text=None, caption=None) -> Message:
    return Message(
        message_id=5, date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text, caption=caption,
    ).as_(bot)


async def _run(session, event, bootstrap_ids=frozenset()):
    """Stages 1, 2 and 4, mounted the way `build_dispatcher` mounts them."""
    seen = {"ran": False}

    async def handler(event, data):
        # `.get`, like every real reader: stage 1 knows nothing about the mode
        # and leaves the key absent, so "no impersonator" is its absence.
        seen.update(ran=True, principal=data["principal"],
                    impersonator=data.get("impersonator"))

    stack = handler
    for middleware in (AccessMiddleware(set(bootstrap_ids)),
                       ImpersonationMiddleware(set(bootstrap_ids)),
                       PrincipalMiddleware(lambda: session,
                                           set(bootstrap_ids))):
        stack = functools.partial(middleware, stack)
    await stack(event, {})
    return seen


def _texts(bot) -> list[str]:
    return [m.text for m in bot.sent if isinstance(m, SendMessage)]


def _admin(session, departed=None):
    session.add(User(last_name="Adminova", first_name="Anna",
                     telegram_id=ADMIN_TID, role=Role.ADMIN,
                     departed_at=departed))


def _student(session, departed=None):
    session.add(User(last_name="Stud", first_name="Sam",
                     matriculation=STUDENT_REF, telegram_id=STUDENT_TID,
                     role=Role.STUDENT, departed_at=departed))


# --- the swap itself ----------------------------------------------------


async def test_an_admin_in_the_mode_is_swapped_for_the_target(session):
    _admin(session)
    _student(session)
    session.commit()
    impersonation.begin(ADMIN_TID, STUDENT_REF)

    seen = await _run(session, _message(FakeBot(), ADMIN_TID, "/me"))

    assert seen["principal"].telegram_id == STUDENT_TID
    assert seen["impersonator"].telegram_id == ADMIN_TID


async def test_a_stale_entry_from_someone_no_longer_an_admin_is_ignored(session):
    """Only /as writes the map and only an admin may run it -- but a `/sync`
    can demote them afterwards, and then the mode must simply stop applying."""
    session.add(User(last_name="Stud", telegram_id=ADMIN_TID,
                     role=Role.STUDENT))
    _student(session)
    session.commit()
    impersonation.begin(ADMIN_TID, STUDENT_REF)
    bot = FakeBot()

    seen = await _run(session, _message(bot, ADMIN_TID, "/me"))

    assert seen["principal"].telegram_id == ADMIN_TID  # not swapped
    assert seen["impersonator"] is None
    # Silently. They asked for their own screen, not for a view of anyone, so
    # "not allowed" would answer a question they did not ask -- on every
    # message until somebody notices the stale entry.
    assert bot.sent == []


async def test_the_entry_survives_a_demotion_rather_than_being_cleared(session):
    """A middleware that ended the mode on the way past would turn a transient
    bad `/sync` into a lost session."""
    session.add(User(last_name="Stud", telegram_id=ADMIN_TID,
                     role=Role.STUDENT))
    _student(session)
    session.commit()
    impersonation.begin(ADMIN_TID, STUDENT_REF)

    await _run(session, _message(FakeBot(), ADMIN_TID, "/me"))

    assert impersonation.ref_for(ADMIN_TID) == STUDENT_REF


# --- an admin the roster dropped ----------------------------------------


async def test_a_departed_admin_cannot_keep_the_bot_through_their_target(session):
    """The hole this ordering has to not open. Stage 4 sees only whoever stage
    2 settled on, so if stage 2 honoured a dropped admin's mode, that admin
    would go on using the whole bot as somebody the roster still lists."""
    _admin(session, departed="2026-07-28")
    _student(session)
    session.commit()
    impersonation.begin(ADMIN_TID, STUDENT_REF)
    bot = FakeBot()

    seen = await _run(session, _message(bot, ADMIN_TID, "/me"))

    assert seen["ran"] is False
    assert _texts(bot) == [DEPARTED_NOTICE]  # refused as themselves, once


async def test_a_bootstrap_admin_the_roster_dropped_keeps_their_own_mode(session):
    """The exemption is a property of the person, so it survives into stage 2:
    BOOTSTRAP_ADMIN_IDS is the way back in when a `/sync` is wrong, and losing
    the view you were debugging it from is the wrong direction."""
    _admin(session, departed="2026-07-28")
    _student(session)
    session.commit()
    impersonation.begin(ADMIN_TID, STUDENT_REF)

    seen = await _run(session, _message(FakeBot(), ADMIN_TID, "/me"),
                      bootstrap_ids={ADMIN_TID})

    assert seen["principal"].telegram_id == STUDENT_TID


# --- a target the roster dropped ----------------------------------------


async def test_a_departed_target_is_refused_in_the_targets_own_words(session):
    """/as exists to show the bot as somebody else sees it, and what a departed
    student sees is exactly this notice -- not a variant of it mentioning the
    mode. The way out is the banner above, which stage 3 already sent."""
    _admin(session)
    _student(session, departed="2026-07-28")
    session.commit()
    impersonation.begin(ADMIN_TID, STUDENT_REF)
    bot = FakeBot()

    seen = await _run(session, _message(bot, ADMIN_TID, "/me"))

    assert seen["ran"] is False
    assert _texts(bot) == [DEPARTED_NOTICE]  # verbatim, nothing appended


async def test_a_departed_target_who_is_bootstrap_shows_the_bot_working(session):
    """What that person actually sees is a working bot, so that is what /as has
    to show. Checking the target without the exemption made this the one corner
    where the mode lied about the thing it exists to reveal."""
    _admin(session)
    _student(session, departed="2026-07-28")
    session.commit()
    impersonation.begin(ADMIN_TID, STUDENT_REF)
    bot = FakeBot()

    seen = await _run(session, _message(bot, ADMIN_TID, "/me"),
                      bootstrap_ids={STUDENT_TID})

    assert seen["ran"] is True
    assert seen["principal"].telegram_id == STUDENT_TID
    assert bot.sent == []


# --- the one exemption stage 2 keeps ------------------------------------


async def test_unas_is_never_impersonated(session):
    _admin(session)
    _student(session, departed="2026-07-28")
    session.commit()
    impersonation.begin(ADMIN_TID, STUDENT_REF)

    seen = await _run(session, _message(FakeBot(), ADMIN_TID, "/unas"))

    # Reaches the handler as the admin, so the command that ends the mode can
    # run even when the target it would swap in is refused.
    assert seen["ran"] is True
    assert seen["principal"].telegram_id == ADMIN_TID


async def test_unas_in_a_photo_caption_is_never_impersonated_either(session):
    """Regression: this read only `.text` while the router read text *or*
    caption, so a `/unas` under a photo was routed to `cmd_unas` by the
    pipeline while the middleware went on impersonating -- and under a departed
    target that meant the refusal instead of the exit. Both sides call
    `pipeline.command_of` now."""
    _admin(session)
    _student(session, departed="2026-07-28")
    session.commit()
    impersonation.begin(ADMIN_TID, STUDENT_REF)

    seen = await _run(session,
                      _message(FakeBot(), ADMIN_TID, caption="/unas"))

    assert seen["ran"] is True
    assert seen["principal"].telegram_id == ADMIN_TID


async def test_unas_addressed_to_the_bot_by_name_is_read_the_same_way(session):
    _admin(session)
    _student(session, departed="2026-07-28")
    session.commit()
    impersonation.begin(ADMIN_TID, STUDENT_REF)

    seen = await _run(session,
                      _message(FakeBot(), ADMIN_TID, "/unas@jbcub_bot"))

    assert seen["ran"] is True


# --- a target that is not there any more --------------------------------


async def test_a_target_whose_row_vanished_ends_the_mode_and_says_so(session):
    """A `/sync` that drops somebody outright rather than marking them
    departed. There is nobody left to be, and the old behaviour -- principal
    None -- met the admin with "You are not linked yet", which is both untrue
    of them and no help at all."""
    _admin(session)
    session.commit()
    impersonation.begin(ADMIN_TID, "30009999")
    bot = FakeBot()

    seen = await _run(session, _message(bot, ADMIN_TID, "/me"))

    assert seen["ran"] is False  # the update was meant for the target
    assert _texts(bot) == [TARGET_GONE]
    assert impersonation.ref_for(ADMIN_TID) is None  # and the mode is over
