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
