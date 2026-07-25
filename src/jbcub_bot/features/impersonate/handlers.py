from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from jbcub_bot.core import identity
from jbcub_bot.core.models import Role, User

router = Router(name="impersonate")

_USAGE = "Usage: /as <matriculation|telegram_id> <query>"


@router.message(Command("as"))
async def cmd_as(message: Message, principal: User, session, bot, dispatcher,
                 command: CommandObject):
    if principal is None or principal.role is not Role.ADMIN:
        await message.answer("Admins only.")
        return

    args = (command.args or "").strip()
    parts = args.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(_USAGE)
        return
    ref, query = parts[0], parts[1].strip()

    target = identity.find_impersonation_target(session, ref)
    if target is None:
        await message.answer(f"No user found for {ref}.")
        return

    await message.answer(f"\U0001f464 Showing as {target.full_name}:")
    new_msg = message.model_copy(
        update={"text": query, "entities": None}
    ).as_(bot)
    await dispatcher.propagate_event(
        "message", new_msg,
        bot=bot, dispatcher=dispatcher, impersonate_ref=ref,
    )
