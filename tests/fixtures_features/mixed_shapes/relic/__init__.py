"""Legacy: `router` + `manifest`, no `register`. Sorts between the two migrated
features so a test can see the loader keep both shapes in one pass.
"""
from aiogram import Router

from jbcub_bot.core.commands import CommandSpec
from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role

router = Router()
manifest = Manifest(
    name="relic",
    commands=[CommandSpec("relic", "Not migrated yet.", Role.STUDENT)],
)
