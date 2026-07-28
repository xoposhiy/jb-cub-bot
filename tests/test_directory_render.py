from jbcub_bot.features.directory.render import (
    EDIT_CALLBACK,
    PRIVACY_CALLBACK,
    me_keyboard,
    render_profile,
)
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.visibility import STAFF_ONLY


def test_render_includes_visible_and_omits_hidden():
    viewer = User(first_name="V", last_name="", role=Role.STUDENT,
                  primary_cohort="2024")
    target = User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                  primary_cohort="2024",
                  handle_observed="ivanov", gmail="i@gmail.com",
                  visibility={"gmail": "nobody"})
    text = render_profile(viewer, target)
    assert "Name: Ivan Ivanov" in text
    assert "ivanov" in text
    assert "i@gmail.com" not in text  # hidden by visibility


def test_render_order_and_labels_are_unchanged():
    # Regression anchor: telegram and status_line moved from unhideable to
    # configurable, and both default to `everyone`, so a stranger's view of a
    # profile must look exactly as it did before the move.
    viewer = User(first_name="V", last_name="Viewer", role=Role.STUDENT,
                  primary_cohort="2024")
    target = User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                  primary_cohort="2021", handle_observed="ivanov",
                  status_line="open to teams", gmail="i@gmail.com")
    assert render_profile(viewer, target) == (
        "Name: Ivan Ivanov\n"
        "Role: Student\n"
        "Cohort: 2021\n"
        "Telegram: @ivanov\n"
        "Status: open to teams"
    )


def test_render_omits_a_hidden_telegram_handle():
    viewer = User(first_name="V", last_name="Viewer", role=Role.STUDENT,
                  primary_cohort="2024")
    target = User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                  primary_cohort="2024", handle_observed="ivanov",
                  visibility={"telegram": STAFF_ONLY})
    text = render_profile(viewer, target)
    assert "ivanov" not in text
    assert "Name: Ivan Ivanov" in text


def test_render_shows_admin_only_fields_to_an_admin():
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    target = User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                  matriculation="30000001", birthday="2000-01-02")
    text = render_profile(admin, target)
    assert "Matriculation: 30000001" in text
    assert "Birthday: 2000-01-02" in text


def test_me_keyboard_offers_editing_and_privacy():
    kb = me_keyboard(User(first_name="S", last_name="Student",
                          role=Role.STUDENT))
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == [
        EDIT_CALLBACK, PRIVACY_CALLBACK,
    ]


def test_me_keyboard_has_nothing_for_a_student_when_not_interactive():
    assert me_keyboard(User(first_name="S", last_name="Student",
                            role=Role.STUDENT), interactive=False) is None


def test_me_keyboard_puts_self_service_above_the_admin_button():
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN,
                 matriculation="30000001")
    kb = me_keyboard(admin)
    assert [b.callback_data for b in kb.inline_keyboard[0]] == [
        EDIT_CALLBACK, PRIVACY_CALLBACK,
    ]
    assert [b.callback_data for b in kb.inline_keyboard[1]] == [
        "dir:admin:30000001",
    ]


def test_me_keyboard_for_an_admin_without_matriculation_has_only_self_service():
    kb = me_keyboard(User(first_name="A", last_name="Admin", role=Role.ADMIN))
    assert len(kb.inline_keyboard) == 1
