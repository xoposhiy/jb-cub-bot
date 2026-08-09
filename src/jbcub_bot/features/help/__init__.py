"""What the bot can do, read off the contract at the moment it is asked.

The handler closes over its own `BotApi` and calls `bot.features()`, so `help`
imports no sibling and reads no process-wide list of manifests -- which is the
whole reason `core/registry.py` can be deleted. Reading at answer time rather
than at registration time is also what lets a feature that registers after the
mount be listed.

The rendering is `render.py`'s, next door. The core collects declarations and
this feature decides how to print them, which is the whole of the split: nothing
in `core/` renders /help, and nothing here decides who may see a line -- that
stays `core/guards.py`'s, and `render` calls it.
"""
from aiogram.types import Message

from jbcub_bot.core.models import User
from jbcub_bot.features.help.render import render_help


def register(bot) -> None:
    bot.describe("❓", "Help", "Commands you can use.")

    @bot.command("help", "List the commands you can use.", public=True)
    async def cmd_help(message: Message, principal: User | None):
        await message.answer(render_help(bot.features(), principal))
