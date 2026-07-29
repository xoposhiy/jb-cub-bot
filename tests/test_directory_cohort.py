from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.render import render_cohort_list
from jbcub_bot.features.directory.visibility import STAFF_ONLY


def _student(first, last, **kw):
    return User(first_name=first, last_name=last, role=Role.STUDENT,
                primary_cohort="2024", **kw)


def test_cohort_list_shows_visible_handles():
    viewer = _student("V", "Viewer")
    mates = [_student("Ivan", "Ivanov", handle_observed="ivanov")]
    assert render_cohort_list(viewer, mates) == "- Ivan Ivanov (@ivanov)"


def test_cohort_list_drops_a_handle_its_owner_hid():
    # The leak this task closes: /cohort used to print handle_observed straight
    # off the model, so `staff_only` on telegram meant nothing here.
    viewer = _student("V", "Viewer")
    mates = [_student("Ivan", "Ivanov", handle_observed="ivanov",
                      visibility={"telegram": STAFF_ONLY})]
    assert render_cohort_list(viewer, mates) == "- Ivan Ivanov"


def test_cohort_list_omits_the_handle_when_there_is_none():
    viewer = _student("V", "Viewer")
    assert render_cohort_list(viewer, [_student("Ivan", "Ivanov")]) == \
        "- Ivan Ivanov"


def test_admin_still_sees_a_hidden_handle_in_the_cohort_list():
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN,
                 primary_cohort="2024")
    mates = [_student("Ivan", "Ivanov", handle_observed="ivanov",
                      visibility={"telegram": STAFF_ONLY})]
    assert render_cohort_list(admin, mates) == "- Ivan Ivanov (@ivanov)"


def test_cohort_list_marks_a_departed_mate_for_the_admin_who_can_see_them():
    # Only an admin is shown a departed person at all, and an unmarked line
    # would read as "still in the cohort".
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN,
                 primary_cohort="2024")
    mates = [_student("Eve", "Expelled", handle_observed="eve",
                      departed_at="2026-07-28")]
    assert render_cohort_list(admin, mates) == \
        "- Eve Expelled (@eve) — ⚠️ departed 2026-07-28"


def test_cohort_list_keeps_one_line_per_mate():
    viewer = _student("V", "Viewer")
    mates = [_student("A", "One", handle_observed="a"), _student("B", "Two")]
    assert render_cohort_list(viewer, mates) == "- A One (@a)\n- B Two"


from types import SimpleNamespace
from unittest.mock import AsyncMock

from jbcub_bot.features.directory.cohort import PICK_PREFIX, cb_pick, cmd_cohort


def _seed(session):
    session.add_all([
        User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
             primary_cohort="2024", matriculation="30000001"),
        User(first_name="Eve", last_name="Expelled", role=Role.STUDENT,
             primary_cohort="2024", matriculation="30000009",
             departed_at="2026-07-28"),
        User(first_name="Old", last_name="Timer", role=Role.STUDENT,
             primary_cohort="2023", matriculation="30000002"),
    ])
    session.commit()


def _msg():
    return SimpleNamespace(answer=AsyncMock(), answer_document=AsyncMock())


def _args(text):
    return SimpleNamespace(args=text)


async def test_a_student_still_gets_one_message_and_no_file(session):
    _seed(session)
    msg = _msg()
    viewer = User(first_name="V", last_name="Viewer", role=Role.STUDENT,
                  primary_cohort="2024")
    await cmd_cohort(msg, principal=viewer, session=session,
                     command=_args("2023"))
    assert "Ivan Ivanov" in msg.answer.await_args.args[0]
    assert "Old Timer" not in msg.answer.await_args.args[0]  # argument ignored
    msg.answer_document.assert_not_awaited()


async def test_staff_with_no_argument_get_a_button_per_cohort(session):
    _seed(session)
    msg = _msg()
    await cmd_cohort(msg, principal=User(last_name="T", role=Role.TEACHER),
                     session=session, command=_args(None))
    keyboard = msg.answer.await_args.kwargs["reply_markup"]
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == ["2024", "2023"]
    payloads = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert payloads[0] == f"{PICK_PREFIX}2024"
    msg.answer_document.assert_not_awaited()


async def test_staff_with_an_argument_get_the_list_and_one_document(session):
    _seed(session)
    msg = _msg()
    await cmd_cohort(msg, principal=User(last_name="A", role=Role.ADMIN),
                     session=session, command=_args(" 2024 "))
    text = msg.answer.await_args.args[0]
    assert "2024" in text and "Ivan Ivanov" in text
    assert "Expelled" not in text  # even for an admin
    document = msg.answer_document.await_args.args[0]
    assert document.filename == "cohort-2024.csv"
    assert b"Expelled" not in document.data
    assert b"Ivanov" in document.data


async def test_an_unknown_cohort_redraws_the_picker_with_a_note(session):
    _seed(session)
    msg = _msg()
    await cmd_cohort(msg, principal=User(last_name="A", role=Role.ADMIN),
                     session=session, command=_args("2019"))
    assert "2019" in msg.answer.await_args.args[0]
    assert msg.answer.await_args.kwargs["reply_markup"] is not None
    msg.answer_document.assert_not_awaited()


async def test_staff_are_told_when_there_are_no_cohorts_at_all(session):
    msg = _msg()
    await cmd_cohort(msg, principal=User(last_name="A", role=Role.ADMIN),
                     session=session, command=_args(None))
    assert "/sync" in msg.answer.await_args.args[0]
    assert msg.answer.await_args.kwargs.get("reply_markup") is None


async def test_a_bootstrap_admin_with_no_row_is_served(session):
    # id is None: identity.apply_bootstrap hands out a transient principal, and
    # nothing here writes, so it must not be refused.
    _seed(session)
    msg = _msg()
    await cmd_cohort(msg, principal=User(last_name="Boot", role=Role.ADMIN),
                     session=session, command=_args("2024"))
    msg.answer_document.assert_awaited_once()


def _cb(data, text="Which cohort?"):
    from unittest.mock import Mock

    from aiogram.types import Message
    # Mock, not AsyncMock: aiogram's Message methods aren't real coroutine
    # functions (inspect.iscoroutinefunction is False on them), so a spec'd
    # AsyncMock wouldn't autodetect edit_text/answer_document as awaitable.
    message = Mock(spec=Message)
    message.text = text
    message.edit_text = AsyncMock()
    message.answer_document = AsyncMock()
    return SimpleNamespace(data=data, message=message, answer=AsyncMock())


async def test_tapping_a_cohort_replaces_the_text_and_sends_the_file(session):
    _seed(session)
    cb = _cb(f"{PICK_PREFIX}2024")
    await cb_pick(cb, principal=User(last_name="A", role=Role.ADMIN),
                  session=session)
    assert "Ivan Ivanov" in cb.message.edit_text.await_args.args[0]
    assert cb.message.edit_text.await_args.kwargs["reply_markup"] is not None
    assert cb.message.answer_document.await_args.args[0].filename == \
        "cohort-2024.csv"
    cb.answer.assert_awaited()


async def test_tapping_the_open_cohort_again_only_resends_the_file(session):
    # Telegram rejects an edit that changes nothing; the file is the point of
    # the tap, so it still goes out.
    _seed(session)
    cb = _cb(f"{PICK_PREFIX}2024")
    await cb_pick(cb, principal=User(last_name="A", role=Role.ADMIN),
                  session=session)
    same = _cb(f"{PICK_PREFIX}2024", text=cb.message.edit_text.await_args.args[0])
    await cb_pick(same, principal=User(last_name="A", role=Role.ADMIN),
                  session=session)
    same.message.edit_text.assert_not_awaited()
    same.message.answer_document.assert_awaited_once()


async def test_a_student_tapping_a_stale_button_is_refused(session):
    _seed(session)
    cb = _cb(f"{PICK_PREFIX}2024")
    await cb_pick(cb, principal=User(last_name="S", role=Role.STUDENT),
                  session=session)
    cb.message.answer_document.assert_not_awaited()
    assert cb.answer.await_args.kwargs.get("show_alert") is True
