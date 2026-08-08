"""What the bot can do, read off the contract at the moment it is asked.

The handler closes over its own `BotApi` and calls `bot.features()`, so `help`
imports no sibling and reads no process-wide list of manifests -- which is the
whole reason `core/registry.py` can be deleted. Reading at answer time rather
than at registration time is also what lets a feature that registers after the
mount be listed.

The rendering is `core/help.py`'s and deliberately not this feature's: what
/help says is a property of the contract, so it is described and tested where
the contract is, and `help` is only the command that prints it.
"""
from aiogram.types import Message

from jbcub_bot.core.help import render_help
from jbcub_bot.core.models import User


def register(bot) -> None:
    bot.describe("❓", "Help", "Commands you can use.")

    @bot.command("help", "List the commands you can use.", public=True)
    async def cmd_help(message: Message, principal: User | None):
        await message.answer(render_help(bot.features(), principal))
