"""Whose dialog is open, and what the feature holding it can do about it.

The FSM here is the real one -- `FSMContext` over `MemoryStorage`, the same
pair a running bot uses -- because the whole point of the façade is that it
behaves like aiogram's storage, and a stub would only prove it behaves like
the stub.
"""
from datetime import datetime, timezone

from aiogram import Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

from jbcub_bot.core.dialogs import (
    Dialog,
    DialogHandle,
    DialogMiddleware,
    state_name,
)

EDIT = DialogHandle("directory", "edit")


class FakeBot:
    def __init__(self):
        self.id = 1
        self.sent: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None


def _dialog(user_id: int = 222) -> Dialog:
    """A dialog on its own in-memory FSM, keyed like aiogram keys one."""
    context = FSMContext(MemoryStorage(),
                         StorageKey(bot_id=1, chat_id=user_id,
                                    user_id=user_id))
    return Dialog(context)


def _message_update(fake_bot, telegram_id: int, text: str, update_id=1) -> Update:
    msg = Message(
        message_id=100 + update_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)
    return Update(update_id=update_id, message=msg).as_(fake_bot)


def _callback_update(fake_bot, telegram_id: int, data: str, update_id=2) -> Update:
    chat = Chat(id=telegram_id, type="private")
    shown = Message(
        message_id=7,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=TgUser(id=1, is_bot=True, first_name="bot"),
        text="whatever was on screen",
    ).as_(fake_bot)
    cb = CallbackQuery(
        id=f"cb-{update_id}",
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        chat_instance="chat-instance",
        data=data,
        message=shown,
    ).as_(fake_bot)
    return Update(update_id=update_id, callback_query=cb).as_(fake_bot)


# --- the derived state name -----------------------------------------------------

def test_a_state_name_is_the_feature_then_the_dialog():
    assert state_name("directory", "edit") == "directory:edit"


def test_two_features_may_each_have_a_dialog_called_edit():
    assert state_name("directory", "edit") != state_name("kb", "edit")


def test_a_handle_knows_the_state_name_its_dialog_runs_under():
    assert EDIT.name == "edit"
    assert EDIT.state == "directory:edit"


# --- the façade ---------------------------------------------------------------

async def test_nobody_owns_a_dialog_until_one_is_started():
    dialog = _dialog()
    assert await dialog.owner() is None
    assert await dialog.data() == {}


async def test_starting_names_the_owner_and_keeps_what_it_was_given():
    dialog = _dialog()
    await dialog.start("directory:edit", field="github", message_id=7)
    assert await dialog.owner() == "directory:edit"
    assert await dialog.data() == {"field": "github", "message_id": 7}


async def test_update_merges_rather_than_replaces():
    dialog = _dialog()
    await dialog.start("directory:edit", field="github")
    await dialog.update(message_id=7)
    assert await dialog.data() == {"field": "github", "message_id": 7}


async def test_end_clears_the_state_and_the_data():
    dialog = _dialog()
    await dialog.start("directory:edit", field="github")
    await dialog.end()
    assert await dialog.owner() is None
    assert await dialog.data() == {}


async def test_ending_when_nothing_is_open_is_not_an_error():
    dialog = _dialog()
    await dialog.end()
    assert await dialog.owner() is None


async def test_another_features_dialog_replaces_the_open_one():
    # One FSM per user, so there is no second slot to keep the first dialog in.
    # Asserted rather than left undefined: the pipeline decides what to do about
    # it, and it can only do that if this is settled.
    dialog = _dialog()
    await dialog.start("directory:edit", field="github")
    await dialog.start("kb:ask", question="when is the exam?")
    assert await dialog.owner() == "kb:ask"
    assert await dialog.data() == {"question": "when is the exam?"}


async def test_a_handle_opens_its_own_dialog():
    dialog = _dialog()
    await EDIT.start(dialog, field="github")
    assert await dialog.owner() == "directory:edit"
    assert await dialog.data() == {"field": "github"}


# --- the middleware -----------------------------------------------------------

async def test_a_callback_opens_the_dialog_the_next_message_is_answering():
    """Both event kinds get a working `dialog`, and it is the same user's one.

    Written as one flow because that is the only thing worth proving: a Dialog
    built from a per-update FSMContext would pass two separate assertions and
    still lose every value the moment the user typed it.
    """
    dp = Dispatcher()
    dp.message.middleware(DialogMiddleware())
    dp.callback_query.middleware(DialogMiddleware())
    seen = {}

    @dp.callback_query()
    async def on_tap(cb: CallbackQuery, dialog: Dialog):
        await EDIT.start(dialog, field="github")

    @dp.message()
    async def on_text(message: Message, dialog: Dialog):
        seen["owner"] = await dialog.owner()
        seen["data"] = await dialog.data()
        await dialog.end()
        seen["after_end"] = await dialog.owner()

    fake_bot = FakeBot()
    tap = _callback_update(fake_bot, 222, "dir:edit:f:github")
    await dp.feed_update(fake_bot, tap, dispatcher=dp)
    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "xoposhiy"),
                         dispatcher=dp)
    assert seen == {"owner": "directory:edit",
                    "data": {"field": "github"},
                    "after_end": None}


async def test_one_users_dialog_is_not_another_users():
    dp = Dispatcher()
    dp.message.middleware(DialogMiddleware())
    owners = []

    @dp.message()
    async def on_text(message: Message, dialog: Dialog):
        owners.append(await dialog.owner())
        await EDIT.start(dialog)

    fake_bot = FakeBot()
    for update_id, telegram_id in ((1, 222), (2, 333), (3, 222)):
        await dp.feed_update(
            fake_bot, _message_update(fake_bot, telegram_id, "hi", update_id),
            dispatcher=dp)
    # The second sender sees no dialog, and saying so did not disturb the first.
    assert owners == [None, None, "directory:edit"]
