"""/as and /unas: entering and leaving the mode.

`register` is the whole of what this feature exports -- no router, no
manifest. It is also what lets /unas split into the two guarantees the old
comment on `cmd_unas` wanted at once: `public=True` means it never refuses,
even the one command an admin needs to get out of a student's view, and
`listed=False` means it never appears in a student's /help without leaving it
undocumented. See `handlers.cmd_unas` for the reasoning in full.
"""
from jbcub_bot.core.models import Role
from jbcub_bot.features.impersonate.handlers import cmd_as, cmd_unas


def register(bot) -> None:
    bot.describe("🕵️", "Impersonate",
                 "Admin: see the bot as a given user (/as <ref>, /unas to return).")

    bot.command("as", "See the bot as another user, until /unas.",
                usage="<ref>", role=Role.ADMIN)(cmd_as)
    bot.command("unas", "Leave the mode entered with /as; return to your own "
                "view.", public=True, listed=False)(cmd_unas)
