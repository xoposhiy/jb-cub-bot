from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role
from jbcub_bot.features.directory.handlers import name_search_intent, router

manifest = Manifest(
    name="directory",
    commands=["start", "me", "cohort", "sync"],
    intents=[name_search_intent],
    min_role=Role.STUDENT,
    help_text="Find classmates and manage your own profile.",
)

__all__ = ["router", "manifest"]
