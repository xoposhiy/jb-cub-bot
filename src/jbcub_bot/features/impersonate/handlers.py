from aiogram.types import Message

from jbcub_bot.core import identity, impersonation
from jbcub_bot.core.dialogs import Dialog
from jbcub_bot.core.models import User

_USAGE = "Usage: /as <matriculation|telegram_id>"
_NOT_IMPERSONATING = "You are not viewing as anyone."


async def cmd_as(message: Message, principal: User, session, dialog: Dialog,
                 arg: str):
    """Enter the mode. Every later update from this admin belongs to the target.

    Inside the mode the principal is the target, so this command runs with the
    target's role like every other: from a student it refuses, and switching
    away is /unas then /as again; from a staff target it goes straight through.
    """
    ref = arg.strip()
    if not ref:
        await message.answer(_USAGE)
        return
    target = identity.find_impersonation_target(session, ref)
    if target is None:
        await message.answer(f"No user found for {ref}.")
        return
    # Whatever half-finished dialog the admin was in is theirs, not the
    # target's: it must not carry over into the view they are about to get.
    await dialog.end()
    impersonation.begin(message.from_user.id,
                        impersonation.canonical_ref(target))
    await message.answer(
        f"\U0001f464 You are now seeing the bot as {target.full_name}.\n"
        "Send /unas to return to your own view."
    )


async def cmd_unas(message: Message, dialog: Dialog):
    """Leave the mode.

    Declared with no `role` and `listed=False`, for the two reasons that used
    to force dodging the command registrar entirely before the contract could
    split them apart: inside the mode the principal is the target, so any rank
    would refuse the one command that gets you out of a student's view -- the
    default guard asks for none. And listing it in /help would put a command in
    that view no student has -- `listed=False` fixes that, without leaving it
    undocumented. Every banner prints it instead.

    It was `public=True` for a while, which is a third thing entirely and was
    never wanted: it meant a stranger the bot had never seen could send /unas
    and be told "You are not viewing as anyone." The default guard says "any
    rank, but somebody we know", which is what this always meant.

    It reads the map rather than `impersonator` because the middleware
    deliberately does not impersonate this command -- see `is_exit_command`.
    """
    if impersonation.end(message.from_user.id) is None:
        await message.answer(_NOT_IMPERSONATING)
        return
    await dialog.end()
    await message.answer("↩️ Back to your own view.")
