"""Who is on the roster, and what each of them chooses to show.

Every command, button, dialog and chain slot of this feature is declared
here: the modules below hold the handlers and the rendering, and none of them
knows how it is reached. That is what took `Router`, `F`, `StateFilter`,
`CommandRegistrar` and `require_linked` out of this package -- an admin-only
button now says so in one line beside its key, where /help and the refusal read
the same declaration.

Two checks stay ordinary code rather than becoming guards, and both ask whether
writing the caller's own row would achieve anything: a bootstrap admin's
principal was never saved (`identity.apply_bootstrap`), and a first-year's row
is a placeholder the next /sync rebuilds (`identity.is_provisional`).
`privacy.cb_cycle`, `edit.on_value` and `edit.cb_clear_do` refuse the write and
say what to do, while every read-only screen lets them look. A guard there
would be a visibility filter, and would quietly shorten /help for exactly the
person who needs to be told something is wrong.
"""
from jbcub_bot.core.contract import TEXT
from jbcub_bot.core.models import Role
from jbcub_bot.core.pipeline import LOOKUP
from jbcub_bot.features.directory import cohort, edit, grades, handlers, privacy
from jbcub_bot.features.directory.render import (
    ADMIN_BACK_CALLBACK,
    ADMIN_CALLBACK,
    EDIT_CALLBACK,
    GRADES_BACK_CALLBACK,
    GRADES_CALLBACK,
    LINK_CALLBACK,
    PRIVACY_CALLBACK,
    PERSON_CALLBACK,
    PROFILE_CALLBACK,
    RESET_CALLBACK,
    RESET_CANCEL_CALLBACK,
    RESET_DO_CALLBACK,
)


def register(bot) -> None:
    bot.describe("📒", "Directory", "Find classmates and manage your own profile.")

    bot.command("me", "Show your own profile.")(handlers.cmd_me)
    bot.command("start", "Start / link your account.",
                public=True)(handlers.cmd_start)
    bot.command("sync", "Re-sync roster from Google Sheets.",
                role=Role.ADMIN)(handlers.cmd_sync)
    bot.command("privacy", "Choose who sees each of your profile fields."
                )(privacy.cmd_privacy)
    bot.command("edit", "Edit your profile fields.")(edit.cmd_edit)
    bot.command("cohort", "List the people in your cohort.")(cohort.cmd_cohort)

    bot.message(at=LOOKUP, when=TEXT,
                description="just type a name — search people",
                )(handlers.name_search)
    # Reached from the search results, so anyone the search answers may tap it.
    bot.button(PERSON_CALLBACK)(handlers.cb_person)

    # Admin actions on somebody else's profile.
    bot.button(ADMIN_CALLBACK, role=Role.ADMIN)(handlers.cb_admin_open)
    bot.button(ADMIN_BACK_CALLBACK, role=Role.ADMIN)(handlers.cb_admin_back)
    bot.button(LINK_CALLBACK, role=Role.ADMIN)(handlers.cb_issue_link)
    bot.button(RESET_CALLBACK, role=Role.ADMIN)(handlers.cb_reset)
    bot.button(RESET_DO_CALLBACK, role=Role.ADMIN)(handlers.cb_reset_do)
    # Nothing to guard: backing out of the confirmation is the safe half of it.
    bot.button(RESET_CANCEL_CALLBACK)(handlers.cb_reset_cancel)

    # The caller's own two screens; each writes only the caller's own row.
    bot.button(PRIVACY_CALLBACK)(privacy.cb_open)
    bot.button(PROFILE_CALLBACK)(privacy.cb_back)
    bot.button(privacy.FIELD_CALLBACK)(privacy.cb_cycle)
    bot.button(EDIT_CALLBACK)(edit.cb_open)
    bot.button(edit.CANCEL_CALLBACK)(edit.cb_cancel)
    bot.button(edit.FIELD_CALLBACK)(edit.cb_field)
    bot.button(edit.CLEAR_CALLBACK)(edit.cb_clear)
    bot.button(edit.CLEAR_DO_CALLBACK)(edit.cb_clear_do)

    # Staff-only screens. TEACHER, not ADMIN: `is_staff` admitted both.
    bot.button(GRADES_CALLBACK, role=Role.TEACHER)(grades.cb_grades)
    bot.button(GRADES_BACK_CALLBACK, role=Role.TEACHER)(grades.cb_grades_back)
    bot.button(cohort.PICK_CALLBACK, role=Role.TEACHER)(cohort.cb_pick)

    # The handle goes back to `edit`, which is where the prompt is opened: a
    # feature holds what `dialog()` gave it rather than writing the state name
    # out and getting it wrong.
    edit.PROMPT = bot.dialog("edit", on_text=edit.on_value,
                             on_cancel=edit.on_cancel)
