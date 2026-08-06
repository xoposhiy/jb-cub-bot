from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from jbcub_bot.core import identity, impersonation
from jbcub_bot.core.commands import CommandRegistrar
from jbcub_bot.core.models import Role, User

router = Router(name="impersonate")
cmd = CommandRegistrar(router)

_USAGE = "Usage: /as <matriculation|telegram_id>"
_NOT_IMPERSONATING = "You are not viewing as anyone."


@cmd.command("as", "See the bot as another user, until /unas.",
             min_role=Role.ADMIN, usage="<ref>")
async def cmd_as(message: Message, principal: User, session,
                 state: FSMContext, command: CommandObject):
    """Enter the mode. Every later update from this admin belongs to the target.

    Inside the mode the principal is the target, so this command refuses like
    any other admin command -- switching target is /unas, then /as again.
    """
    ref = (command.args or "").strip()
    if not ref:
        await message.answer(_USAGE)
        return
    target = identity.find_impersonation_target(session, ref)
    if target is None:
        await message.answer(f"No user found for {ref}.")
        return
    # Whatever half-finished dialog the admin was in is theirs, not the
    # target's: it must not carry over into the view they are about to get.
    await state.clear()
    impersonation.begin(message.from_user.id,
                        impersonation.canonical_ref(target))
    await message.answer(
        f"\U0001f464 You are now seeing the bot as {target.full_name}.\n"
        "Send /unas to return to your own view."
    )


@router.message(Command("unas"))
async def cmd_unas(message: Message, state: FSMContext):
    """Leave the mode.

    Registered straight on the router rather than through CommandRegistrar for
    two reasons: inside the mode the principal is a student, so a role-guarded
    command would refuse the one command that gets you out; and listing it in
    /help would put a command in the student's view that no student has. Every
    banner prints it instead.

    It reads the map rather than `impersonator` because the middleware
    deliberately does not impersonate this command -- see `is_exit_command`.
    """
    if impersonation.end(message.from_user.id) is None:
        await message.answer(_NOT_IMPERSONATING)
        return
    await state.clear()
    await message.answer("↩️ Back to your own view.")
