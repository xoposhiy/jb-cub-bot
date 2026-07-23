from aiogram import Router

from sdt_bot.core.loader import Manifest
from sdt_bot.core.models import Role

router = Router()
manifest = Manifest(
    name="dummy",
    commands=["ping"],
    min_role=Role.STUDENT,
    help_text="a dummy feature",
)
