from aiogram import Router
from aiogram.types import Message

from jbcub_bot.core import registry
from jbcub_bot.core.commands import CommandRegistrar
from jbcub_bot.core.models import User
from jbcub_bot.features.help.render import render_help

router = Router(name="help")
cmd = CommandRegistrar(router)


@cmd.command("help", "List the commands you can use.", public=True)
async def cmd_help(message: Message, principal: User, session):
    await message.answer(render_help(registry.all_manifests(), principal))
