from sdt_bot.features.directory.render import admin_keyboard
from sdt_bot.core.models import User


def test_admin_keyboard_has_link_and_reset():
    kb = admin_keyboard(User(name="Ivan", matriculation="30000001"))
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "dir:link:30000001" in datas
    assert "dir:reset:30000001" in datas


def test_admin_keyboard_none_without_matriculation():
    assert admin_keyboard(User(name="Staff")) is None
