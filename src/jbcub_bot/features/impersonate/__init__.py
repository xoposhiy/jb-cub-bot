from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role
from jbcub_bot.features.impersonate.handlers import router

manifest = Manifest(
    name="impersonate",
    commands=["as"],
    intents=[],
    min_role=Role.ADMIN,
    help_text="Admin: see the bot as a given user (/as <ref> <query>).",
)

__all__ = ["router", "manifest"]
