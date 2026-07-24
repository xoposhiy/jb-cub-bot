from jbcub_bot.features.directory.visibility import (
    are_cohort_mates,
    visible_fields,
)
from jbcub_bot.core.models import Role, User


def _u(**kw):
    return User(last_name=kw.pop("last_name", "U"),
                first_name=kw.pop("first_name", ""), **kw)


def test_cohort_mates_by_intersection():
    a = _u(primary_cohort="2024", past_cohorts=["2023"])
    b = _u(primary_cohort="2022", past_cohorts=["2023"])
    c = _u(primary_cohort="2021", past_cohorts=[])
    assert are_cohort_mates(a, b) is True   # shared 2023
    assert are_cohort_mates(a, c) is False


def test_student_sees_cohort_mate_configurable_by_default():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", gmail="t@gmail.com",
                github="gh", visibility={})  # default -> cohort
    fields = visible_fields(viewer, target)
    assert fields["gmail"] == "t@gmail.com"
    assert fields["github"] == "gh"


def test_student_non_cohort_sees_super_minimum_only():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2021", gmail="t@gmail.com",
                handle_observed="tg")
    fields = visible_fields(viewer, target)
    assert "gmail" not in fields
    assert fields["telegram"] == "tg"
    assert fields["last_name"] == target.last_name
    assert fields["first_name"] == target.first_name


def test_field_hidden_when_level_nobody():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", gmail="t@gmail.com",
                visibility={"gmail": "nobody"})
    assert "gmail" not in visible_fields(viewer, target)


def test_field_all_students_visible_across_cohorts():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2021", github="gh",
                visibility={"github": "all_students"})
    assert visible_fields(viewer, target)["github"] == "gh"


def test_teacher_sees_full_set_across_cohorts_ignoring_nobody():
    viewer = _u(role=Role.TEACHER, primary_cohort="9999")
    target = _u(role=Role.STUDENT, primary_cohort="2021", gmail="t@gmail.com",
                visibility={"gmail": "nobody"})
    assert visible_fields(viewer, target)["gmail"] == "t@gmail.com"


def test_admin_sees_admin_only_fields():
    viewer = _u(role=Role.ADMIN)
    target = _u(role=Role.STUDENT, matriculation="30000001")
    assert visible_fields(viewer, target)["matriculation"] == "30000001"


def test_student_never_sees_admin_only():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", matriculation="30000001")
    assert "matriculation" not in visible_fields(viewer, target)


def test_teacher_never_sees_admin_only():
    viewer = _u(role=Role.TEACHER, primary_cohort="9999")
    target = _u(role=Role.STUDENT, primary_cohort="2021", matriculation="30000001")
    assert "matriculation" not in visible_fields(viewer, target)


def test_admin_overrides_nobody_configurable():
    viewer = _u(role=Role.ADMIN)
    target = _u(role=Role.STUDENT, primary_cohort="2021", gmail="t@gmail.com",
                visibility={"gmail": "nobody"})
    assert visible_fields(viewer, target)["gmail"] == "t@gmail.com"
