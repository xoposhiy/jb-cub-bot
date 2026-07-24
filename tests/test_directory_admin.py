from types import SimpleNamespace
from unittest.mock import AsyncMock

from jbcub_bot.features.directory.render import admin_keyboard
from jbcub_bot.features.directory.handlers import cb_issue_link, cb_reset
from jbcub_bot.core.models import Role, User


def test_admin_keyboard_has_link_and_reset():
    kb = admin_keyboard(User(last_name="Ivan", matriculation="30000001"))
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "dir:link:30000001" in datas
    assert "dir:reset:30000001" in datas


def test_admin_keyboard_none_without_matriculation():
    assert admin_keyboard(User(last_name="Staff")) is None


async def test_cb_reset_denied_for_non_admin(session):
    target = User(
        last_name="Ivan",
        matriculation="30000001",
        telegram_id=111,
        role=Role.STUDENT,
    )
    session.add(target)
    session.commit()

    non_admin = User(last_name="Petya", role=Role.STUDENT, telegram_id=222)

    cb = SimpleNamespace(data="dir:reset:30000001", answer=AsyncMock())
    await cb_reset(cb, principal=non_admin, session=session)
    cb.answer.assert_awaited_once_with("Admins only.", show_alert=True)
    assert target.telegram_id == 111

    cb2 = SimpleNamespace(data="dir:reset:30000001", answer=AsyncMock())
    await cb_reset(cb2, principal=None, session=session)
    cb2.answer.assert_awaited_once_with("Admins only.", show_alert=True)
    assert target.telegram_id == 111


async def test_cb_reset_admin_clears_binding(session):
    target = User(
        last_name="Ivan",
        matriculation="30000001",
        telegram_id=111,
        role=Role.STUDENT,
    )
    session.add(target)
    session.commit()

    admin = User(last_name="Admin", role=Role.ADMIN, telegram_id=999)

    cb = SimpleNamespace(data="dir:reset:30000001", answer=AsyncMock())
    await cb_reset(cb, principal=admin, session=session)

    assert target.telegram_id is None
    cb.answer.assert_awaited_once()


async def test_cb_issue_link_denied_for_non_admin(session):
    target = User(last_name="Ivan", matriculation="30000001", role=Role.STUDENT)
    session.add(target)
    session.commit()

    non_admin = User(last_name="Petya", role=Role.STUDENT, telegram_id=222)

    cb = SimpleNamespace(
        data="dir:link:30000001",
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
        bot=SimpleNamespace(me=AsyncMock()),
    )
    await cb_issue_link(cb, principal=non_admin, session=session)

    cb.answer.assert_awaited_once_with("Admins only.", show_alert=True)
    cb.message.answer.assert_not_awaited()
    cb.bot.me.assert_not_awaited()

    cb2 = SimpleNamespace(data="dir:link:30000001", answer=AsyncMock())
    await cb_issue_link(cb2, principal=None, session=session)
    cb2.answer.assert_awaited_once_with("Admins only.", show_alert=True)


async def test_cb_issue_link_not_found(session):
    admin = User(last_name="Admin", role=Role.ADMIN, telegram_id=999)

    cb = SimpleNamespace(
        data="dir:link:DOESNOTEXIST",
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
        bot=SimpleNamespace(me=AsyncMock()),
    )
    await cb_issue_link(cb, principal=admin, session=session)

    cb.answer.assert_awaited_once_with("Not found.", show_alert=True)
    cb.message.answer.assert_not_awaited()
    cb.bot.me.assert_not_awaited()
