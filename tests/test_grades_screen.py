from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import Message

from jbcub_bot.core.models import Grade, Role, User
from jbcub_bot.features.directory import grades
from jbcub_bot.features.directory.render import profile_keyboard
from jbcub_bot.features.directory.screens import EXPIRED


def _student_with_grades(session):
    user = User(
        first_name="Ivan",
        last_name="Ivanov",
        role=Role.STUDENT,
        matriculation="30000001",
        primary_cohort="2024",
    )
    session.add(user)
    session.commit()
    session.add_all([
        Grade(user_id=user.id, cohort="2024", term="Fall 2025",
              category="Mandatory", label="Math", value="91%", position=3),
        Grade(user_id=user.id, cohort="2024", term="Fall 2025",
              category="Mandatory", label="CS", value="4.33", position=4),
        Grade(user_id=user.id, cohort="2024", term="Spring 2026",
              category="Methods", label="Physics", value="pass", position=6),
    ])
    session.commit()
    return user


def _callback(data, text=None):
    """A callback whose message rejects an edit that changes nothing, as Telegram does."""
    message = Mock(spec=Message)
    message.text = text

    async def edit_text(new_text, **kwargs):
        if new_text == message.text:
            raise TelegramBadRequest(
                method=EditMessageText(chat_id=1, message_id=1, text=new_text),
                message="Bad Request: message is not modified: specified new "
                        "message content and reply markup are exactly the same "
                        "as a current content and reply markup of the message",
            )
        message.text = new_text

    message.edit_text = AsyncMock(side_effect=edit_text)
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


def test_has_grades_grouping_and_position_order(session):
    user = _student_with_grades(session)
    assert grades.has_grades(session, user.id) is True
    groups = grades.group_by_term(grades.load_grades(session, user.id))
    assert list(groups) == ["Fall 2025", "Spring 2026"]
    assert [grade.label for grade in groups["Fall 2025"]] == ["Math", "CS"]


def test_profile_button_present_for_staff_absent_for_student(session):
    target = _student_with_grades(session)
    for role in (Role.TEACHER, Role.ADMIN):
        keyboard = profile_keyboard(
            User(last_name="Viewer", role=role), target, show_grades=True
        )
        data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        assert "dir:grades:30000001:-1" in data
    assert profile_keyboard(
        User(last_name="Viewer", role=Role.STUDENT), target, show_grades=True
    ) is None


def _button_texts(cb):
    markup = cb.message.edit_text.await_args.kwargs["reply_markup"]
    return [button.text for row in markup.inline_keyboard for button in row]


async def test_open_latest_and_switch_to_explicit_term(session):
    _student_with_grades(session)
    admin = User(last_name="Admin", role=Role.ADMIN)
    latest = _callback("dir:grades:30000001:-1")
    await grades.cb_grades(latest, principal=admin, session=session)
    assert latest.message.edit_text.await_args.args[0].startswith("Spring 2026")
    assert "Physics: pass" in latest.message.edit_text.await_args.args[0]
    assert _button_texts(latest)[:2] == ["Fall 2025", "📍 Spring 2026"]

    earlier = _callback("dir:grades:30000001:0")
    await grades.cb_grades(earlier, principal=admin, session=session)
    assert earlier.message.edit_text.await_args.args[0].startswith("Fall 2025")
    assert "Math: 91%" in earlier.message.edit_text.await_args.args[0]
    assert _button_texts(earlier)[:2] == ["📍 Fall 2025", "Spring 2026"]


def test_marking_the_open_semester_leaves_callback_data_alone():
    keyboard = grades.semester_keyboard(
        "30000001", ["Fall 2025", "Spring 2026"], active="Spring 2026"
    )
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert [(button.text, button.callback_data) for button in buttons] == [
        ("Fall 2025", "dir:grades:30000001:0"),
        ("📍 Spring 2026", "dir:grades:30000001:1"),
        ("⬅️ Back", "dir:grades_back:30000001"),
    ]


async def test_tapping_the_semester_already_on_screen_changes_nothing(session):
    """The profile button opens the latest term, so its own button is a repeat tap."""
    _student_with_grades(session)
    admin = User(last_name="Admin", role=Role.ADMIN)
    opened = _callback("dir:grades:30000001:-1")
    await grades.cb_grades(opened, principal=admin, session=session)

    again = _callback("dir:grades:30000001:1", text=opened.message.text)
    await grades.cb_grades(again, principal=admin, session=session)
    again.message.edit_text.assert_not_awaited()
    again.answer.assert_awaited_once_with()


async def test_stale_index_and_student_are_refused(session):
    _student_with_grades(session)
    stale = _callback("dir:grades:30000001:7")
    await grades.cb_grades(
        stale, principal=User(last_name="Admin", role=Role.ADMIN), session=session
    )
    stale.answer.assert_awaited_once_with(EXPIRED, show_alert=True)
    stale.message.edit_text.assert_not_awaited()

    denied = _callback("dir:grades:30000001:-1")
    await grades.cb_grades(
        denied, principal=User(last_name="Student", role=Role.STUDENT), session=session
    )
    denied.answer.assert_awaited_once_with("Staff only.", show_alert=True)


async def test_bootstrap_admin_and_back_to_profile(session):
    target = _student_with_grades(session)
    target.source_link = "ABC"
    session.commit()
    admin = User(last_name="Bootstrap", role=Role.ADMIN)

    opened = _callback("dir:grades:30000001:-1")
    await grades.cb_grades(opened, principal=admin, session=session)
    opened.message.edit_text.assert_awaited_once()

    back = _callback("dir:grades_back:30000001")
    await grades.cb_grades_back(back, principal=admin, session=session)
    kwargs = back.message.edit_text.await_args.kwargs
    assert kwargs["entities"]
    data = [
        button.callback_data
        for row in kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert "dir:grades:30000001:-1" in data
