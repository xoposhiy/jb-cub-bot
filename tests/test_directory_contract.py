"""What `directory` declares, asked of the declaration itself.

Every other directory test calls a handler or feeds a `Dispatcher`. This one
goes through `register(bot)`, because that is where the guards, the button keys
and the dialog live now: five `role is not ADMIN` checks, three `is_staff`
checks and `require_linked` all used to be the first two lines of a handler, so
a test that called the handler proved them. Called bare, the same handler
proves nothing -- the assertions those tests carried are here instead, on the
real registrations, reached through the real entry points.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message
from aiogram.types import User as TgUser

from jbcub_bot.core.buttons import match, take_callback
from jbcub_bot.core.contract import TEXT, Registry
from jbcub_bot.core.dialogs import Dialog
from jbcub_bot.core.guards import ADMIN_REFUSAL, NOT_LINKED, STAFF_REFUSAL
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.pipeline import LOOKUP, take_message
from jbcub_bot.features.directory import cohort, edit, grades, handlers, privacy
from jbcub_bot.features.directory import register

STUDENT = User(last_name="Ivanov", first_name="Ivan", role=Role.STUDENT,
               primary_cohort="2024")
TEACHER = User(last_name="Petrova", first_name="Petra", role=Role.TEACHER)
ADMIN = User(last_name="Egorov", first_name="Egor", role=Role.ADMIN)


class FakeBot:
    def __init__(self):
        self.id = 1
        self.sent: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None


def _registry() -> Registry:
    registry = Registry()
    register(registry.api_for("directory"))
    return registry


def _message(text: str) -> Message:
    """A real `Message` with `answer` replaced, so the refusal is readable
    without a bot: what is under test is who gets turned away, not the send."""
    message = Message(message_id=1, date=datetime.now(timezone.utc),
                      chat=Chat(id=777, type="private"),
                      from_user=TgUser(id=777, is_bot=False, first_name="tg"),
                      text=text).as_(FakeBot())
    object.__setattr__(message, "answer", AsyncMock())
    return message


def _callback(data: str) -> SimpleNamespace:
    return SimpleNamespace(data=data, answer=AsyncMock(),
                           message=SimpleNamespace(edit_text=AsyncMock(),
                                                   edit_reply_markup=AsyncMock(),
                                                   answer=AsyncMock()))


def _dialog(user_id: int = 777) -> Dialog:
    return Dialog(FSMContext(MemoryStorage(),
                             StorageKey(bot_id=1, chat_id=user_id,
                                        user_id=user_id)))


async def _send(registry, message, principal, dialog=None, session=None):
    return await take_message(registry, message, principal=principal,
                              session=session, bot=message.bot,
                              impersonator=None, dialog=dialog or _dialog(),
                              oplog=None)


async def _tap(registry, callback, principal, session=None):
    return await take_callback(registry, callback, principal=principal,
                               session=session, bot=None, impersonator=None,
                               dialog=_dialog(), oplog=None)


# --- the guards that used to be the first two lines of a handler ---------------

async def test_sync_refuses_a_student_and_an_unlinked_caller():
    """Was `test_directory_sync.test_sync_denied_for_non_admin`, which called
    the wrapper `CommandRegistrar` handed back. `role=Role.ADMIN` on the
    registration is what refuses now."""
    for principal, expected in ((STUDENT, ADMIN_REFUSAL), (None, NOT_LINKED)):
        message = _message("/sync")
        await _send(_registry(), message, principal)
        message.answer.assert_awaited_once_with(expected)


ADMIN_TAPS = ["dir:admin:30000001", "dir:admin_back:30000001",
              "dir:link:30000001", "dir:reset:30000001",
              "dir:reset_do:30000001"]
STAFF_TAPS = ["dir:grades:30000001:-1", "dir:grades_back:30000001",
              "dir:cohort:2024"]


@pytest.mark.parametrize("data", ADMIN_TAPS)
async def test_an_admin_button_refuses_a_student_and_an_unlinked_caller(data):
    """Was five copies of `principal is None or principal.role is not
    Role.ADMIN` inside `handlers.py`, asserted by five direct calls."""
    for principal, expected in ((STUDENT, ADMIN_REFUSAL), (None, NOT_LINKED)):
        callback = _callback(data)
        await _tap(_registry(), callback, principal)
        callback.answer.assert_awaited_once_with(expected, show_alert=True)


@pytest.mark.parametrize("data", STAFF_TAPS)
async def test_a_staff_button_refuses_a_student_and_admits_a_teacher(data):
    """Was three copies of `not is_staff(principal)`. A teacher passing is half
    the assertion: `role=Role.TEACHER` must not read as "admins only"."""
    callback = _callback(data)
    await _tap(_registry(), callback, STUDENT)
    callback.answer.assert_awaited_once_with(STAFF_REFUSAL, show_alert=True)

    allowed = _callback(data)
    await _tap(_registry(), allowed, TEACHER, session=_NoRows())
    assert STAFF_REFUSAL not in str(allowed.answer.await_args)


class _NoRows:
    """A session that finds nobody: a staff handler past its guard is allowed
    to look something up, and this test is about the guard, not the lookup."""

    def scalar(self, *args, **kwargs):
        return None

    def scalars(self, *args, **kwargs):
        return SimpleNamespace(all=lambda: [])


# --- one meaning of `public`, whichever kind declares it -----------------------

async def test_public_lets_an_unlinked_caller_through_on_a_command_and_the_chain():
    registry = _registry()
    start = _message("/start")
    await _send(registry, start, None)
    assert NOT_LINKED not in start.answer.await_args.args[0]

    # The same guard on the chain handler, and the reason it is public: an
    # unlinked caller who types a name is told what to do rather than left with
    # "No one found."
    typed = _message("Ivanov")
    await _send(registry, typed, None)
    typed.answer.assert_awaited_once_with(NOT_LINKED)


async def test_no_public_refuses_an_unlinked_caller_on_a_command_button_dialog():
    registry = _registry()
    command = _message("/me")
    await _send(registry, command, None)
    command.answer.assert_awaited_once_with(NOT_LINKED)

    callback = _callback("dir:privacy")
    await _tap(registry, callback, None)
    callback.answer.assert_awaited_once_with(NOT_LINKED, show_alert=True)

    dialog = _dialog()
    await edit.PROMPT.start(dialog, field="github")
    value = _message("alice")
    await _send(registry, value, None, dialog=dialog)
    value.answer.assert_awaited_once_with(NOT_LINKED)


# --- the longest registered key wins -------------------------------------------

EXPECTED_KEYS = [
    ("dir:edit", "cb_open", ""),
    ("dir:edit:cancel", "cb_cancel", ""),
    ("dir:edit:f:github", "cb_field", "github"),
    ("dir:edit:clear:github", "cb_clear", "github"),
    ("dir:edit:clear_do:github", "cb_clear_do", "github"),
    ("dir:admin:30000001", "cb_admin_open", "30000001"),
    ("dir:admin_back:30000001", "cb_admin_back", "30000001"),
    ("dir:link:30000001", "cb_issue_link", "30000001"),
    ("dir:reset:30000001", "cb_reset", "30000001"),
    ("dir:reset_do:30000001", "cb_reset_do", "30000001"),
    ("dir:reset_cancel", "cb_reset_cancel", ""),
    ("dir:privacy", "cb_open", ""),
    ("dir:profile", "cb_back", ""),
    ("dir:vis:gmail", "cb_cycle", "gmail"),
    ("dir:grades:30000001:-1", "cb_grades", "30000001:-1"),
    ("dir:grades_back:30000001", "cb_grades_back", "30000001"),
    ("dir:cohort:2024", "cb_pick", "2024"),
]


@pytest.mark.parametrize("data, handler, arg", EXPECTED_KEYS)
def test_a_tap_reaches_the_longest_key_that_prefixes_it(data, handler, arg):
    """`dir:edit` must not swallow `dir:edit:cancel`, and `dir:reset` must not
    swallow `dir:reset_do` -- which is the whole reason the core matches on the
    longest key rather than the first."""
    spec, payload = match(_registry().buttons(), data)
    assert (spec.handler.__name__, payload) == (handler, arg)


def test_every_declared_key_is_covered_by_the_table_above():
    """A key added without a case here would go untested silently."""
    declared = {spec.key for spec in _registry().buttons()}
    covered = {match(_registry().buttons(), data)[0].key
               for data, _, _ in EXPECTED_KEYS}
    assert declared == covered


# --- the dialog ----------------------------------------------------------------

def test_the_dialog_is_declared_with_both_hooks():
    [spec] = _registry().features()[0].dialogs
    assert (spec.name, spec.on_text, spec.on_cancel) == \
        ("edit", edit.on_value, edit.on_cancel)
    assert edit.PROMPT.state == "directory:edit"


async def test_a_command_beats_the_open_prompt():
    """`~F.text.startswith("/")` is gone from `on_value`, and this is what used
    to need it: a command typed while a prompt is open is still a command."""
    registry = _registry()
    dialog = _dialog()
    await edit.PROMPT.start(dialog, field="github")

    await _send(registry, _message("/me"), STUDENT, dialog=dialog)

    assert await dialog.owner() == "directory:edit"  # /me did not end it


# --- the six commands and the one chain slot -----------------------------------

def test_the_six_commands_are_declared_and_cancel_is_not_among_them():
    registry = _registry()
    assert list(registry.commands()) == [
        "me", "start", "sync", "privacy", "edit", "cohort",
    ]


def test_the_name_search_sits_at_lookup():
    [spec] = _registry().chain()
    assert (spec.at, spec.when, spec.handler) == (LOOKUP, TEXT,
                                                  handlers.name_search)
    assert spec.description == "just type a name — search people"


def test_the_feature_describes_itself():
    [reg] = _registry().features()
    assert (reg.description.emoji, reg.description.title) == ("📒", "Directory")
    assert reg.description.summary == \
        "Find classmates and manage your own profile."


def test_the_registration_passes_every_validate_rule():
    _registry().validate()


# --- the modules a migrated feature no longer needs ----------------------------

def test_no_module_keeps_a_router_or_a_registrar():
    for module in (handlers, privacy, edit, grades, cohort):
        assert not hasattr(module, "router"), module.__name__
        assert not hasattr(module, "cmd"), module.__name__
