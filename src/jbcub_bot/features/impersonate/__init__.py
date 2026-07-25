from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role
from jbcub_bot.features.impersonate.handlers import cmd, router

manifest = Manifest(
    name="impersonate",
    commands=cmd.specs,
    intents=[],
    min_role=Role.ADMIN,
    help_text="Admin: see the bot as a given user (/as <ref> <query>).",
    emoji="🕵️",
)

__all__ = ["router", "manifest"]
