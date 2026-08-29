"""What /help says: grouped by feature, with the role on the line."""
from jbcub_bot.core.contract import Registry
from jbcub_bot.features.help.render import (
    CONTACT_NOTICE,
    UNLINKED_NOTICE,
    render_help,
)
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.pipeline import LOOKUP


async def _noop(message):
    return None


def _student():
    return User(last_name="S", role=Role.STUDENT)


def _teacher():
    return User(last_name="T", role=Role.TEACHER)


def _admin():
    return User(last_name="A", role=Role.ADMIN)


def _directory(registry: Registry):
    """A feature with a line for everyone: the shape /help is built for."""
    bot = registry.api_for("directory")
    bot.describe("📒", "Directory", "Find classmates and manage your own profile.")
    bot.command("me", "Show your profile.")(_noop)
    bot.command("grades", "Show a student's grades.", usage="<ref>",
                role=Role.TEACHER)(_noop)
    bot.command("sync", "Refresh the roster from Sheets.", role=Role.ADMIN)(_noop)
    bot.command("start", "Link your account.", public=True)(_noop)
    bot.message(at=LOOKUP,
                description="A classmate's name finds their profile.")(_noop)
    return bot


def _impersonate(registry: Registry):
    """Every line elevated -- the feature whose heading never rendered."""
    bot = registry.api_for("impersonate")
    bot.describe("🕵️", "Impersonate", "Admin: see the bot as a given user.")
    bot.command("as", "See the bot as another user, until /unas.", usage="<ref>",
                role=Role.ADMIN)(_noop)
    bot.command("unas", "Stop impersonating.", public=True, listed=False)(_noop)
    return bot


def _both() -> Registry:
    registry = Registry()
    _directory(registry)
    _impersonate(registry)
    return registry


# --- grouping -----------------------------------------------------------------

def test_a_feature_is_one_block_under_its_own_heading():
    out = render_help(_both().features(), _student())
    assert out.startswith(
        "📒 Directory — Find classmates and manage your own profile.\n"
        "  /me — Show your profile.\n"
    )


def test_features_are_rendered_in_registration_order():
    out = render_help(_both().features(), _admin())
    assert out.index("📒 Directory") < out.index("🕵️ Impersonate")


def test_blocks_are_separated_by_a_blank_line():
    out = render_help(_both().features(), _admin())
    assert "\n\n🕵️ Impersonate" in out


def test_an_all_elevated_feature_still_gets_its_heading():
    out = render_help(_both().features(), _admin())
    assert ("🕵️ Impersonate — Admin: see the bot as a given user.\n"
            "  /as <ref> — See the bot as another user, until /unas. (admin)") in out


def test_an_elevated_line_stays_under_its_own_features_heading():
    out = render_help(_both().features(), _admin())
    # The pooled "🔐 Admin" block is what this replaces: /sync belongs to
    # directory, so it renders before impersonate's heading, not after it.
    assert out.index("/sync") < out.index("🕵️ Impersonate")
    assert "🔐 Admin" not in out


def test_a_feature_with_nothing_visible_gets_no_heading():
    out = render_help(_both().features(), _student())
    assert "🕵️" not in out
    assert "/as" not in out


# --- the badge ----------------------------------------------------------------

def test_admin_only_lines_carry_the_admin_badge():
    out = render_help(_both().features(), _admin())
    assert "  /sync — Refresh the roster from Sheets. (admin)" in out


def test_staff_only_lines_carry_the_staff_badge():
    out = render_help(_both().features(), _admin())
    assert "  /grades <ref> — Show a student's grades. (staff)" in out


def test_a_line_anyone_may_use_carries_no_badge():
    out = render_help(_both().features(), _admin())
    assert "  /me — Show your profile.\n" in out
    assert "  /start — Link your account.\n" in out


def test_a_chain_line_carries_the_badge_too():
    registry = Registry()
    bot = registry.api_for("kb")
    bot.describe("📚", "Knowledge base", "Ask about the program.")
    bot.message(at=200, description="A question gets an answer.",
                role=Role.ADMIN)(_noop)
    out = render_help(registry.features(), _admin())
    assert "  💬 A question gets an answer. (admin)" in out


# --- what filtering is allowed to look at -------------------------------------

def test_a_student_sees_neither_elevated_line():
    out = render_help(_both().features(), _student())
    assert "/sync" not in out
    assert "/grades" not in out
    assert "(admin)" not in out
    assert "(staff)" not in out


def test_a_teacher_sees_the_staff_line_but_not_the_admin_one():
    out = render_help(_both().features(), _teacher())
    assert "/grades" in out
    assert "/sync" not in out


def test_an_unlisted_command_is_absent_for_the_principal_its_guard_allows():
    # /unas is public=True -- its guard refuses nobody -- and listed=False, so
    # it must never appear for anyone. Its own line, not the string: /as
    # mentions /unas in its description, and that is documentation, not a line.
    for principal in (None, _student(), _admin()):
        assert "  /unas" not in render_help(_both().features(), principal)


def test_usage_is_omitted_when_empty():
    out = render_help(_both().features(), _admin())
    assert "  /sync — " in out
    assert "  /grades <ref> — " in out


# --- the chain line -----------------------------------------------------------

def test_a_chain_entry_is_listed_with_its_description():
    out = render_help(_both().features(), _student())
    assert "  💬 A classmate's name finds their profile." in out


def test_a_chain_entry_with_no_description_is_not_listed():
    registry = Registry()
    bot = registry.api_for("kb")
    bot.describe("📚", "Knowledge base", "Ask about the program.")
    bot.command("kb", "Show what the base holds.")(_noop)
    bot.message(at=200)(_noop)
    out = render_help(registry.features(), _student())
    assert "💬" not in out


# --- notes --------------------------------------------------------------------

def test_a_string_note_is_a_line_of_its_own():
    registry = Registry()
    bot = registry.api_for("kb")
    bot.describe("📚", "Knowledge base", "Ask about the program.")
    bot.note("The base holds the handbook.")
    out = render_help(registry.features(), _student())
    assert out == ("📚 Knowledge base — Ask about the program.\n"
                   "  The base holds the handbook.\n\n"
                   f"{CONTACT_NOTICE}")


def test_a_callable_note_is_resolved_with_the_principal():
    registry = Registry()
    bot = registry.api_for("kb")
    bot.describe("📚", "Knowledge base", "Ask about the program.")
    bot.note(lambda principal: f"Asked as {principal.last_name}.")
    out = render_help(registry.features(), _student())
    assert "  Asked as S." in out


def test_a_callable_note_sees_None_for_an_unlinked_caller():
    registry = Registry()
    bot = registry.api_for("kb")
    bot.describe("📚", "Knowledge base", "Ask about the program.")
    bot.note(lambda principal: f"Principal is {principal}.", public=True)
    out = render_help(registry.features(), None)
    assert "  Principal is None." in out


def _admin_note() -> Registry:
    registry = Registry()
    bot = registry.api_for("kb")
    bot.describe("📚", "Knowledge base", "Ask about the program.")
    bot.command("kb", "Show what the base holds.")(_noop)
    bot.note("Reload with /kb_reload.", role=Role.ADMIN)
    return registry


def test_an_admin_note_is_hidden_from_a_student():
    out = render_help(_admin_note().features(), _student())
    assert "Reload" not in out
    assert "  /kb — Show what the base holds." in out


def test_a_note_carries_no_badge():
    # A note is prose, not something to type: it says who it is for itself, and
    # its guard already keeps it away from anyone else.
    out = render_help(_admin_note().features(), _admin())
    assert out == ("📚 Knowledge base — Ask about the program.\n"
                   "  /kb — Show what the base holds.\n"
                   "  Reload with /kb_reload.\n\n"
                   f"{CONTACT_NOTICE}")


def test_a_note_resolving_to_nothing_is_not_a_blank_line():
    # kb's note lists what is in the base; an empty base has nothing to say, and
    # a bare "  " inside the block would read as a rendering fault.
    registry = Registry()
    bot = registry.api_for("kb")
    bot.describe("📚", "Knowledge base", "Ask about the program.")
    bot.command("kb", "Show what the base holds.")(_noop)
    bot.note(lambda principal: "")
    out = render_help(registry.features(), _student())
    assert out == ("📚 Knowledge base — Ask about the program.\n"
                   "  /kb — Show what the base holds.\n\n"
                   f"{CONTACT_NOTICE}")


# --- /cancel ------------------------------------------------------------------

def _editing() -> Registry:
    registry = Registry()
    bot = registry.api_for("directory")
    bot.describe("📒", "Directory", "Find classmates.")
    bot.command("me", "Show your profile.")(_noop)
    bot.dialog("edit", on_text=_noop)
    return registry


def test_cancel_is_listed_under_a_feature_that_registered_a_dialog():
    out = render_help(_editing().features(), _student())
    assert out == ("📒 Directory — Find classmates.\n"
                   "  /me — Show your profile.\n"
                   "  /cancel — Cancel what you are in the middle of.\n\n"
                   f"{CONTACT_NOTICE}")


def test_cancel_is_absent_from_a_feature_with_no_dialog():
    out = render_help(_both().features(), _admin())
    assert "/cancel" not in out


def test_cancel_takes_the_dialogs_guard():
    registry = Registry()
    bot = registry.api_for("impersonate")
    bot.describe("🕵️", "Impersonate", "Admin: see the bot as a given user.")
    bot.dialog("pick", on_text=_noop, role=Role.ADMIN)
    assert render_help(registry.features(), _student()) == CONTACT_NOTICE
    assert ("  /cancel — Cancel what you are in the middle of. (admin)"
            in render_help(registry.features(), _admin()))


def test_cancel_is_listed_once_however_many_dialogs_a_feature_has():
    registry = Registry()
    bot = registry.api_for("directory")
    bot.describe("📒", "Directory", "Find classmates.")
    bot.dialog("edit", on_text=_noop)
    bot.dialog("comment", on_text=_noop)
    out = render_help(registry.features(), _student())
    assert out.count("/cancel") == 1


# --- the unlinked -------------------------------------------------------------

def test_an_unlinked_caller_sees_the_public_lines_and_the_notice():
    out = render_help(_both().features(), None)
    assert out == ("📒 Directory — Find classmates and manage your own profile.\n"
                   "  /start — Link your account.\n"
                   "\n"
                   "You're not linked yet — ask a program admin for a "
                   "one-time link.\n"
                   "\n"
                   f"{CONTACT_NOTICE}")
    assert "/me" not in out
    assert "💬" not in out


def test_an_unlinked_caller_with_nothing_public_sees_the_notice_alone():
    registry = Registry()
    _impersonate(registry)
    assert render_help(registry.features(), None) == (
        f"{UNLINKED_NOTICE}\n\n{CONTACT_NOTICE}"
    )


def test_the_notice_wording_is_unchanged():
    assert UNLINKED_NOTICE == (
        "You're not linked yet — ask a program admin for a one-time link."
    )
