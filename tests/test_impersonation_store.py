"""Who each admin is currently viewing the bot as."""

from types import SimpleNamespace

from aiogram.types import CallbackQuery, User as TgUser

from jbcub_bot.core import impersonation


def test_nobody_is_impersonating_by_default():
    assert impersonation.ref_for(777) is None


def test_begin_then_ref_for_returns_the_target():
    impersonation.begin(777, "30000001")
    assert impersonation.ref_for(777) == "30000001"


def test_one_admins_mode_does_not_leak_to_another():
    impersonation.begin(777, "30000001")
    assert impersonation.ref_for(778) is None


def test_end_returns_the_ref_and_clears_it():
    impersonation.begin(777, "30000001")
    assert impersonation.end(777) == "30000001"
    assert impersonation.ref_for(777) is None


def test_end_without_a_mode_is_not_an_error():
    assert impersonation.end(777) is None


def test_reset_clears_every_admin():
    impersonation.begin(777, "30000001")
    impersonation.begin(778, "30000002")
    impersonation.reset()
    assert impersonation.ref_for(777) is None
    assert impersonation.ref_for(778) is None


def test_is_exit_command_is_false_for_a_callback_query():
    # A CallbackQuery has no .text at all -- getattr's default must carry the
    # guard through rather than raising, or every button inside the mode
    # would break with nothing to catch it.
    cb = CallbackQuery(
        id="cb-1",
        from_user=TgUser(id=777, is_bot=False, first_name="Admin"),
        chat_instance="chat",
        data="dir:privacy",
    )
    assert impersonation.is_exit_command(cb) is False


def test_is_exit_command_is_true_for_unas():
    assert impersonation.is_exit_command(SimpleNamespace(text="/unas")) is True
