from jbcub_bot.core.loader import Manifest
from jbcub_bot.features.help.handlers import cmd, router

manifest = Manifest(
    name="help",
    commands=cmd.specs,
    intents=[],
    help_text="Commands you can use.",
    emoji="❓",
)

__all__ = ["router", "manifest"]
