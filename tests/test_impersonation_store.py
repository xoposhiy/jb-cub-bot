"""Who each admin is currently viewing the bot as."""

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User as TgUser

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
    # A CallbackQuery carries no command at all, and `command_of` is written
    # for messages -- so the guard turns it away by type rather than by
    # reading it, or every button inside the mode would break with nothing to
    # catch it.
    cb = CallbackQuery(
        id="cb-1",
        from_user=TgUser(id=777, is_bot=False, first_name="Admin"),
        chat_instance="chat",
        data="dir:privacy",
    )
    assert impersonation.is_exit_command(cb) is False


def _message(text=None, caption=None) -> Message:
    """A real Message, not a stand-in: what is under test is that this reads a
    command exactly as `pipeline.command_of` does, and a namespace with only
    the attribute today's code happens to touch would not show that."""
    return Message(
        message_id=1, date=datetime.now(timezone.utc),
        chat=Chat(id=777, type="private"),
        from_user=TgUser(id=777, is_bot=False, first_name="Admin"),
        text=text, caption=caption,
    )


@pytest.mark.parametrize("kwargs, expected", [
    ({"text": "/unas"}, True),
    # Telegram appends the bot's name to a command tapped in a group.
    ({"text": "/unas@jbcub_bot"}, True),
    # Read from the caption too, the way the router reads it. Looking only at
    # .text here once meant a `/unas` under a photo left the mode on but ran
    # `cmd_unas` anyway.
    ({"caption": "/unas"}, True),
    ({"text": "/me"}, False),
    ({"text": "unas"}, False),  # not addressed to the bot as a command
    ({"text": "/unassign"}, False),
    ({"text": None}, False),  # a photo with no caption at all
])
def test_is_exit_command_reads_a_message_the_way_the_router_will(kwargs,
                                                                 expected):
    assert impersonation.is_exit_command(_message(**kwargs)) is expected
