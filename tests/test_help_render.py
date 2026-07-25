from jbcub_bot.features.help.render import render_help

from jbcub_bot.core.commands import CommandSpec
from jbcub_bot.core.intents import Intent
from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role, User


def _directory():
    return Manifest(
        name="directory",
        emoji="📒",
        help_text="Find classmates and manage your own profile.",
        commands=[
            CommandSpec("me", "Show your own profile."),
            CommandSpec("cohort", "List the people in your cohort."),
            CommandSpec("sync", "Re-sync roster from Google Sheets.",
                        min_role=Role.ADMIN),
            CommandSpec("start", "Start / link your account.", public=True),
        ],
        intents=[Intent("directory.search", r".+", handler=None,
                        description="just type a name — search people")],
        min_role=Role.STUDENT,
    )


def _impersonate():
    return Manifest(
        name="impersonate", emoji="🕵️",
        help_text="Admin: see the bot as a given user.",
        commands=[CommandSpec("as", "View the bot as another user.",
                              min_role=Role.ADMIN, usage="<ref> <query>")],
        min_role=Role.ADMIN,
    )


def _manifests():
    return [_directory(), _impersonate()]


def test_student_sees_baseline_no_admin_section():
    out = render_help(_manifests(), User(last_name="S", role=Role.STUDENT))
    assert "/me — Show your own profile." in out
    assert "/cohort — List the people in your cohort." in out
    assert "💬 just type a name — search people" in out
    assert "🔐 Admin" not in out
    assert "/sync" not in out
    assert "/as" not in out


def test_admin_sees_admin_section_with_elevated_commands():
    out = render_help(_manifests(), User(last_name="A", role=Role.ADMIN))
    assert "🔐 Admin" in out
    assert "/sync — Re-sync roster from Google Sheets." in out
    assert "/as <ref> <query> — View the bot as another user." in out
    # elevated commands are NOT duplicated under their feature header
    assert out.index("/sync") > out.index("🔐 Admin")


def test_admin_still_sees_baseline():
    out = render_help(_manifests(), User(last_name="A", role=Role.ADMIN))
    assert "/me — Show your own profile." in out


def test_feature_header_rendered():
    out = render_help(_manifests(), User(last_name="S", role=Role.STUDENT))
    assert "📒 Directory — Find classmates and manage your own profile." in out


def test_unlinked_sees_only_public_and_notice():
    out = render_help(_manifests(), None)
    assert "/start — Start / link your account." in out
    assert "/me" not in out
    assert "💬" not in out
    assert "🔐 Admin" not in out
    assert "You're not linked yet — ask a program admin for a one-time link." in out


def test_import():
    # ensure the function is importable at module top
    assert callable(render_help)
