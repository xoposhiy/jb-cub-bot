from aiogram import Router

from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role

router = Router()
manifest = Manifest(
    name="dummy",
    commands=["ping"],
    min_role=Role.STUDENT,
    help_text="a dummy feature",
)
