from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role
from jbcub_bot.features.directory import privacy
from jbcub_bot.features.directory.handlers import cmd, name_search_intent, router

# The privacy screen keeps its own router so it can live in its own module;
# the loader only ever sees the feature's single top-level router.
router.include_router(privacy.router)

manifest = Manifest(
    name="directory",
    commands=cmd.specs + privacy.cmd.specs,
    intents=[name_search_intent],
    min_role=Role.STUDENT,
    help_text="Find classmates and manage your own profile.",
)

__all__ = ["router", "manifest"]
