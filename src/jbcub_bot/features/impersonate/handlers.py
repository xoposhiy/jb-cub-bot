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

    Declared `public=True` and `listed=False` rather than an ordinary
    role-guarded command, for the two reasons that used to force dodging the
    command registrar entirely before the contract could split them apart:
    inside the mode the principal is the target, so a role guard would refuse
    the one command that gets you out of a student's view -- `public=True`
    fixes that, since it refuses nobody. And listing it in /help would put a
    command in that view no student has -- `listed=False` fixes that, without
    leaving it undocumented. Every banner prints it instead.

    It reads the map rather than `impersonator` because the middleware
    deliberately does not impersonate this command -- see `is_exit_command`.
    """
    if impersonation.end(message.from_user.id) is None:
        await message.answer(_NOT_IMPERSONATING)
        return
    await dialog.end()
    await message.answer("↩️ Back to your own view.")
