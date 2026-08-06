from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.render import (
    EDIT_CALLBACK,
    PRIVACY_CALLBACK,
    me_keyboard,
    profile_entities,
    profile_keyboard,
    render_profile,
)
from jbcub_bot.features.directory.visibility import STAFF_ONLY


def test_profile_keyboard_combines_grades_and_admin_rows_for_an_admin():
    viewer = User(last_name="Admin", role=Role.ADMIN)
    target = User(last_name="Ivanov", role=Role.STUDENT,
                  matriculation="30000001")
    keyboard = profile_keyboard(viewer, target, show_grades=True)
    data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert data == ["dir:grades:30000001:-1", "dir:admin:30000001"]


def test_profile_keyboard_shows_grades_for_teacher_but_not_student():
    target = User(last_name="Ivanov", matriculation="30000001")
    teacher = User(last_name="Teacher", role=Role.TEACHER)
    student = User(last_name="Student", role=Role.STUDENT)
    assert profile_keyboard(teacher, target, show_grades=True) is not None
    assert profile_keyboard(student, target, show_grades=True) is None


def test_profile_keyboard_omits_grades_when_target_has_none():
    viewer = User(last_name="Admin", role=Role.ADMIN)
    target = User(last_name="Ivanov", matriculation="30000001")
    keyboard = profile_keyboard(viewer, target, show_grades=False)
    data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert data == ["dir:admin:30000001"]


def test_render_profile_gives_cohortless_staff_a_source_line_for_admin():
    admin = User(last_name="Admin", role=Role.ADMIN)
    target = User(last_name="Teacher", role=Role.TEACHER, source_link="RIGHTS")
    assert "Source: Rights sheet" in render_profile(admin, target)
    assert "Source:" not in render_profile(User(last_name="S"), target)


def test_profile_entities_link_exact_cohort_value_in_utf16_units():
    admin = User(last_name="Admin", role=Role.ADMIN)
    target = User(
        first_name="Eve",
        last_name="Expelled",
        primary_cohort="sdt-2023-2026",
        source_link="ABC123",
        departed_at="2026-07-28",
    )
    text = render_profile(admin, target)
    entities = profile_entities(admin, target, text)
    assert len(entities) == 1
    entity = entities[0]
    value = target.primary_cohort
    prefix = text[:text.index(f"Cohort: {value}")] + "Cohort: "
    assert entity.offset == len(prefix.encode("utf-16-le")) // 2
    assert entity.length == len(value.encode("utf-16-le")) // 2
    assert entity.url == "https://docs.google.com/spreadsheets/d/ABC123"


def test_profile_entities_link_rights_fallback_and_hide_from_non_admin():
    admin = User(last_name="Admin", role=Role.ADMIN)
    target = User(last_name="Teacher", role=Role.TEACHER, source_link="RIGHTS")
    text = render_profile(admin, target)
    entity = profile_entities(admin, target, text)[0]
    prefix = text[:text.index("Source: Rights sheet")] + "Source: "
    assert entity.offset == len(prefix.encode("utf-16-le")) // 2
    assert entity.length == len("Rights sheet".encode("utf-16-le")) // 2
    assert profile_entities(User(last_name="Student"), target, text) == []


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


def test_render_leads_with_the_departed_marker_for_an_admin():
    # An admin who searched someone up must see they are gone before reading
    # anything else on the profile -- the data below it stopped updating.
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    target = User(first_name="Eve", last_name="Expelled", role=Role.STUDENT,
                  primary_cohort="2024", departed_at="2026-07-28")
    assert render_profile(admin, target).startswith(
        "⚠️ Departed: 2026-07-28\nName: Eve Expelled")


def test_me_keyboard_offers_editing_and_privacy():
    kb = me_keyboard(User(first_name="S", last_name="Student",
                          role=Role.STUDENT))
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == [
        EDIT_CALLBACK, PRIVACY_CALLBACK,
    ]


def test_me_keyboard_offers_the_self_service_buttons():
    kb = me_keyboard(
        User(first_name="S", last_name="Student", role=Role.STUDENT),
    )
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == [
        EDIT_CALLBACK,
        PRIVACY_CALLBACK,
    ]


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
