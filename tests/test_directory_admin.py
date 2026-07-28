from types import SimpleNamespace
from unittest.mock import AsyncMock

from jbcub_bot.features.directory.render import admin_keyboard
from jbcub_bot.features.directory.handlers import (
    cb_admin_back,
    cb_admin_open,
    cb_issue_link,
    cb_reset,
    cb_reset_do,
)
from jbcub_bot.core.models import Role, User


def test_admin_keyboard_hides_actions_behind_one_button():
    kb = admin_keyboard(User(last_name="Ivan", matriculation="30000001"))
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert datas == ["dir:admin:30000001"]


async def test_cb_admin_open_offers_reset_when_linked(session):
    """A linked profile gets Reset, not Invite: linking is exclusive."""
    session.add(User(last_name="Ivan", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()
    admin = User(last_name="Admin", role=Role.ADMIN, telegram_id=999)

    cb = SimpleNamespace(
        data="dir:admin:30000001",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_reply_markup=AsyncMock()),
    )
    await cb_admin_open(cb, principal=admin, session=session)

    kb = cb.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert datas == ["dir:reset:30000001", "dir:admin_back:30000001"]
    cb.answer.assert_awaited_once()


async def test_cb_admin_open_offers_invite_when_not_linked(session):
    session.add(User(last_name="Ivan", matriculation="30000001",
                     telegram_id=None, role=Role.STUDENT))
    session.commit()
    admin = User(last_name="Admin", role=Role.ADMIN, telegram_id=999)

    cb = SimpleNamespace(
        data="dir:admin:30000001",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_reply_markup=AsyncMock()),
    )
    await cb_admin_open(cb, principal=admin, session=session)

    kb = cb.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert datas == ["dir:link:30000001", "dir:admin_back:30000001"]


async def test_cb_admin_open_not_found(session):
    admin = User(last_name="Admin", role=Role.ADMIN, telegram_id=999)

    cb = SimpleNamespace(
        data="dir:admin:DOESNOTEXIST",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_reply_markup=AsyncMock()),
    )
    await cb_admin_open(cb, principal=admin, session=session)

    cb.answer.assert_awaited_once_with("Not found.", show_alert=True)
    cb.message.edit_reply_markup.assert_not_awaited()


async def test_cb_admin_back_collapses_again(session):
    admin = User(last_name="Admin", role=Role.ADMIN, telegram_id=999)

    cb = SimpleNamespace(
        data="dir:admin_back:30000001",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_reply_markup=AsyncMock()),
    )
    await cb_admin_back(cb, principal=admin, session=session)

    kb = cb.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert datas == ["dir:admin:30000001"]


async def test_cb_admin_back_on_own_profile_restores_me_buttons(session):
    admin = User(last_name="Admin", role=Role.ADMIN, telegram_id=999,
                 matriculation="30000009")

    cb = SimpleNamespace(
        data="dir:admin_back:30000009",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_reply_markup=AsyncMock()),
    )
    await cb_admin_back(cb, principal=admin, session=session)

    kb = cb.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert datas == ["dir:edit", "dir:privacy", "dir:admin:30000009"]


async def test_cb_admin_open_denied_for_non_admin(session):
    non_admin = User(last_name="Petya", role=Role.STUDENT, telegram_id=222)

    cb = SimpleNamespace(
        data="dir:admin:30000001",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_reply_markup=AsyncMock()),
    )
    await cb_admin_open(cb, principal=non_admin, session=session)
    cb.answer.assert_awaited_once_with("Admins only.", show_alert=True)
    cb.message.edit_reply_markup.assert_not_awaited()

    cb2 = SimpleNamespace(
        data="dir:admin:30000001",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_reply_markup=AsyncMock()),
    )
    await cb_admin_open(cb2, principal=None, session=session)
    cb2.answer.assert_awaited_once_with("Admins only.", show_alert=True)
    cb2.message.edit_reply_markup.assert_not_awaited()


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


async def test_cb_reset_admin_confirms_before_clearing(session):
    target = User(
        last_name="Ivan",
        matriculation="30000001",
        telegram_id=111,
        role=Role.STUDENT,
    )
    session.add(target)
    session.commit()

    admin = User(last_name="Admin", role=Role.ADMIN, telegram_id=999)

    # Step 1: pressing "Reset" only asks for confirmation; nothing is cleared.
    cb = SimpleNamespace(
        data="dir:reset:30000001",
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
    )
    await cb_reset(cb, principal=admin, session=session)
    assert target.telegram_id == 111  # untouched until confirmed
    cb.message.answer.assert_awaited_once()
    kb = cb.message.answer.await_args.kwargs["reply_markup"]
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "dir:reset_do:30000001" in datas
    assert "dir:reset_cancel" in datas

    # Step 2: confirming actually clears the binding.
    cb2 = SimpleNamespace(
        data="dir:reset_do:30000001",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )
    await cb_reset_do(cb2, principal=admin, session=session)
    assert target.telegram_id is None
    cb2.answer.assert_awaited_once()

    # …and hands over the invite, the reason the reset was done at all.
    kb = cb2.message.edit_text.await_args.kwargs["reply_markup"]
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert datas == ["dir:link:30000001"]


async def test_cb_issue_link_refused_while_the_profile_is_still_linked(session):
    """Reset is where handing the profile to someone else gets confirmed.

    The menu hides Invite on a linked profile, but an older message still
    carries the button — pressing it must not quietly move the profile.
    """
    target = User(first_name="I", last_name="Ivan", matriculation="30000001",
                  telegram_id=111, role=Role.STUDENT)
    session.add(target)
    session.commit()
    admin = User(last_name="Admin", role=Role.ADMIN, telegram_id=999)

    cb = SimpleNamespace(
        data="dir:link:30000001",
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
        bot=SimpleNamespace(me=AsyncMock()),
    )
    await cb_issue_link(cb, principal=admin, session=session)

    alert = cb.answer.await_args.args[0]
    assert "I Ivan" in alert and "Reset telegram_id" in alert
    cb.message.answer.assert_not_awaited()  # no link handed out
    assert target.link_nonce is None  # no token burned either


async def test_cb_issue_link_refused_for_someone_the_roster_dropped(session):
    # The invite would be accepted by Telegram and then refused by the bot, so
    # the admin has to hear "no" here rather than after sending a dead link.
    target = User(first_name="E", last_name="Expelled",
                  matriculation="30000009", role=Role.STUDENT,
                  departed_at="2026-07-28")
    session.add(target)
    session.commit()
    admin = User(last_name="Admin", role=Role.ADMIN, telegram_id=999)

    cb = SimpleNamespace(
        data="dir:link:30000009",
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
        bot=SimpleNamespace(me=AsyncMock()),
    )
    await cb_issue_link(cb, principal=admin, session=session)

    alert = cb.answer.await_args.args[0]
    assert "E Expelled" in alert and "roster" in alert.lower()
    cb.message.answer.assert_not_awaited()  # no link handed out
    assert target.link_nonce is None  # no token burned either


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


async def test_cb_issue_link_explains_what_the_invite_does(session, monkeypatch):
    from jbcub_bot.features.directory import handlers

    monkeypatch.setattr(
        handlers, "get_settings",
        lambda: SimpleNamespace(link_secret="s3cret", link_ttl_seconds=86400),
    )
    target = User(last_name="Ivan", matriculation="30000001", role=Role.STUDENT)
    session.add(target)
    session.commit()

    admin = User(last_name="Admin", role=Role.ADMIN, telegram_id=999)
    cb = SimpleNamespace(
        data="dir:link:30000001",
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
        bot=SimpleNamespace(me=AsyncMock(return_value=SimpleNamespace(username="jbcub"))),
    )
    await cb_issue_link(cb, principal=admin, session=session)

    text = cb.message.answer.await_args.args[0]
    assert "https://t.me/jbcub?start=" in text
    assert "works once" in text  # the link is explained, not just pasted
    assert "24h" in text


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
