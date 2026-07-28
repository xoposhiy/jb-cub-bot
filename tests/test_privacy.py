from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.privacy import (
    FIELD_CALLBACK_PREFIX,
    privacy_keyboard,
    render_privacy,
)
from jbcub_bot.features.directory.render import PROFILE_CALLBACK
from jbcub_bot.features.directory.visibility import (
    COHORT,
    EVERYONE,
    LEVEL_EMOJI,
    STAFF_ONLY,
)


def _me(**kw):
    return User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                primary_cohort="2024", **kw)


def test_screen_explains_the_levels_and_the_unhideable_fields():
    text = render_privacy(_me())
    assert "Who sees your data" in text
    for level in (STAFF_ONLY, COHORT, EVERYONE):
        assert LEVEL_EMOJI[level] in text
    assert "Staff only" in text
    assert "My cohort" in text
    assert "Everyone" in text
    assert "Name, role and cohort are always visible." in text


def test_screen_lists_every_configurable_field_with_its_level():
    me = _me(handle_observed="ivanov", gmail="i@gmail.com",
             visibility={"github": STAFF_ONLY})
    text = render_privacy(me)
    assert f"{LEVEL_EMOJI[EVERYONE]} Telegram: @ivanov" in text
    assert f"{LEVEL_EMOJI[COHORT]} Gmail: i@gmail.com" in text
    assert f"{LEVEL_EMOJI[STAFF_ONLY]} GitHub: —" in text


def test_screen_shows_an_empty_field_as_a_dash():
    # github/codeforces are in no sheet mapping yet, so they are empty for
    # everyone -- the level is still worth setting ahead of time.
    text = render_privacy(_me())
    assert "Codeforces: —" in text


def test_screen_truncates_a_long_status():
    me = _me(status_line="x" * 80)
    line = next(l for l in render_privacy(me).splitlines() if "Status:" in l)
    assert len(line) < 80
    assert line.endswith("…")


def test_screen_never_mentions_an_admin_only_field():
    me = _me(matriculation="30000001", birthday="2000-01-02", comment="note")
    text = render_privacy(me)
    assert "30000001" not in text
    assert "Matriculation" not in text
    assert "Birthday" not in text
    assert "Comment" not in text


def test_keyboard_puts_two_fields_per_row_and_back_alone():
    kb = privacy_keyboard(_me())
    widths = [len(row) for row in kb.inline_keyboard]
    assert widths == [2, 2, 2, 1]  # 6 configurable fields, then Back
    assert kb.inline_keyboard[-1][0].callback_data == PROFILE_CALLBACK
    assert kb.inline_keyboard[-1][0].text == "← Back to profile"


def test_keyboard_buttons_carry_the_field_and_show_its_level():
    kb = privacy_keyboard(_me(visibility={"gmail": STAFF_ONLY}))
    buttons = {b.callback_data: b.text
               for row in kb.inline_keyboard for b in row}
    assert buttons[f"{FIELD_CALLBACK_PREFIX}telegram"] == \
        f"Telegram {LEVEL_EMOJI[EVERYONE]}"
    assert buttons[f"{FIELD_CALLBACK_PREFIX}gmail"] == \
        f"Gmail {LEVEL_EMOJI[STAFF_ONLY]}"


def test_staff_configure_their_own_fields_too():
    # Admins and teachers are ordinary rows with the same configurable fields;
    # nothing about the screen is student-only.
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN,
                 gmail="a@gmail.com")
    assert "Gmail: a@gmail.com" in render_privacy(admin)
    assert len(privacy_keyboard(admin).inline_keyboard) == 4


def test_every_callback_data_fits_telegram_s_64_byte_limit():
    kb = privacy_keyboard(_me())
    for row in kb.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= 64
