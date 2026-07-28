import pytest

from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.visibility import (
    COHORT,
    EVERYONE,
    STAFF_ONLY,
    Category,
    are_cohort_mates,
    editable_column,
    field_value,
    level_of,
    next_level,
    set_level,
    visible_fields,
)
from jbcub_bot.features.directory import visibility


def _u(**kw):
    return User(last_name=kw.pop("last_name", "U"),
                first_name=kw.pop("first_name", ""), **kw)


# --- the field table -------------------------------------------------------

def test_configurable_fields_are_the_six_expected_ones():
    names = [f.name for f in visibility.CONFIGURABLE_FIELDS]
    assert names == ["telegram", "status_line", "gmail", "cubemail",
                     "github", "codeforces"]


def test_every_configurable_field_has_a_default_and_others_do_not():
    for spec in visibility.FIELDS:
        if spec.category is Category.CONFIGURABLE:
            assert spec.default in visibility.LEVELS, spec.name
        else:
            assert spec.default is None, spec.name


def test_field_order_matches_the_rendered_profile_order():
    assert [f.name for f in visibility.FIELDS] == [
        "first_name", "last_name", "role", "primary_cohort",
        "telegram", "telegram_id", "status_line",
        "gmail", "cubemail", "github", "codeforces",
        "matriculation", "birthday", "citizenship", "comment",
    ]


def test_editable_column_is_the_self_column_for_two_source_fields():
    assert editable_column(visibility.BY_NAME["github"]) == "github_self"
    assert editable_column(visibility.BY_NAME["codeforces"]) == "codeforces_self"
    assert editable_column(visibility.BY_NAME["status_line"]) == "status_line"


def test_editable_fields_are_the_three_a_user_owns():
    assert [f.name for f in visibility.EDITABLE_FIELDS] == [
        "status_line", "github", "codeforces",
    ]


def test_every_editable_field_is_configurable_and_has_a_hint():
    # An editable ALWAYS field could not be hidden; an editable ADMIN_ONLY one
    # would tell its owner it exists.
    for spec in visibility.EDITABLE_FIELDS:
        assert spec.category is Category.CONFIGURABLE, spec.name
        assert spec.edit_hint, spec.name


# --- levels ---------------------------------------------------------------

def test_next_level_cycles_and_wraps():
    assert next_level(STAFF_ONLY) == COHORT
    assert next_level(COHORT) == EVERYONE
    assert next_level(EVERYONE) == STAFF_ONLY


def test_level_of_falls_back_to_the_per_field_default():
    u = _u(visibility={})
    assert level_of(u, "telegram") == EVERYONE
    assert level_of(u, "status_line") == EVERYONE
    assert level_of(u, "gmail") == COHORT


def test_level_of_reads_legacy_values():
    u = _u(visibility={"gmail": "nobody", "github": "all_students"})
    assert level_of(u, "gmail") == STAFF_ONLY
    assert level_of(u, "github") == EVERYONE


def test_level_of_ignores_a_value_it_cannot_understand():
    u = _u(visibility={"gmail": "friends-of-friends"})
    assert level_of(u, "gmail") == COHORT  # the field's default


def test_set_level_reassigns_the_dict_so_sqlalchemy_sees_it(session):
    u = _u(telegram_id=1, visibility={})
    session.add(u)
    session.commit()
    before = u.visibility
    set_level(u, "gmail", STAFF_ONLY)
    assert u.visibility is not before  # a new dict, not an in-place mutation
    session.commit()
    session.expire(u)
    assert u.visibility == {"gmail": STAFF_ONLY}


def test_set_level_keeps_other_fields():
    u = _u(visibility={"gmail": COHORT})
    set_level(u, "github", EVERYONE)
    assert u.visibility == {"gmail": COHORT, "github": EVERYONE}


def test_field_value_renders_telegram_with_an_at_sign():
    assert field_value(_u(handle_observed="tg"), "telegram") == "@tg"
    assert field_value(_u(handle_sheet="sheet"), "telegram") == "@sheet"
    assert field_value(_u(), "telegram") is None
    assert field_value(_u(gmail="a@b.c"), "gmail") == "a@b.c"


def test_field_value_prefers_the_self_reported_account():
    assert field_value(_u(github_self="alice", github_sheet=None), "github") == "alice"
    assert field_value(_u(github_self=None, github_sheet="alice"), "github") == "alice"
    assert field_value(_u(github_self="alice", github_sheet="alice"), "github") == "alice"
    assert field_value(_u(), "github") is None


def test_field_value_shows_the_roster_value_next_to_a_differing_own_one():
    u = _u(github_self="alice-dev", github_sheet="alice")
    assert field_value(u, "github") == "alice-dev (roster: alice)"


def test_field_value_treats_a_blank_sheet_cell_as_missing():
    # normalize_rows writes "" for an empty cell, not None.
    assert field_value(_u(codeforces_self="alice", codeforces_sheet=""),
                       "codeforces") == "alice"


# --- cohort mates ---------------------------------------------------------

def test_cohort_mates_by_intersection():
    a = _u(primary_cohort="2024", past_cohorts=["2023"])
    b = _u(primary_cohort="2022", past_cohorts=["2023"])
    c = _u(primary_cohort="2021", past_cohorts=[])
    assert are_cohort_mates(a, b) is True   # shared 2023
    assert are_cohort_mates(a, c) is False


# --- visible_fields -------------------------------------------------------

def test_student_sees_cohort_mate_configurable_by_default():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", gmail="t@gmail.com",
                github_sheet="gh", visibility={})  # default -> cohort
    fields = visible_fields(viewer, target)
    assert fields["gmail"] == "t@gmail.com"
    assert fields["github"] == "gh"


def test_student_non_cohort_sees_telegram_but_not_gmail():
    # telegram defaults to `everyone`, gmail to `cohort` -- this is the whole
    # point of per-field defaults.
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2021", gmail="t@gmail.com",
                handle_observed="tg", status_line="hi")
    fields = visible_fields(viewer, target)
    assert fields["telegram"] == "@tg"
    assert fields["status_line"] == "hi"
    assert "gmail" not in fields
    assert fields["last_name"] == target.last_name
    assert fields["first_name"] == target.first_name


def test_cub_email_is_as_private_as_gmail():
    # The roster owns it, but it is still a personal contact detail, so it must
    # follow gmail's cohort default rather than being readable program-wide.
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2021",
                cubemail="i@constructor.university")
    assert level_of(target, "cubemail") == COHORT
    assert "cubemail" not in visible_fields(viewer, target)
    mate = _u(role=Role.STUDENT, primary_cohort="2021")
    assert visible_fields(mate, target)["cubemail"] == "i@constructor.university"


def test_owner_may_hide_their_cub_email_from_their_cohort():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024",
                cubemail="i@constructor.university",
                visibility={"cubemail": STAFF_ONLY})
    assert "cubemail" not in visible_fields(viewer, target)


def test_cub_email_is_not_editable_because_the_roster_owns_it():
    assert visibility.BY_NAME["cubemail"].editable is False


def test_student_cannot_see_a_staff_only_field():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", gmail="t@gmail.com",
                visibility={"gmail": STAFF_ONLY})
    assert "gmail" not in visible_fields(viewer, target)


def test_student_cannot_see_a_hidden_telegram_handle():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", handle_observed="tg",
                visibility={"telegram": STAFF_ONLY})
    assert "telegram" not in visible_fields(viewer, target)


def test_everyone_level_crosses_cohorts():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2021", github_sheet="gh",
                visibility={"github": EVERYONE})
    assert visible_fields(viewer, target)["github"] == "gh"


def test_owner_always_sees_their_own_staff_only_field():
    # Regression: hiding a field used to hide it from its owner's own /me.
    me = _u(role=Role.STUDENT, primary_cohort="2024", gmail="t@gmail.com",
            visibility={"gmail": STAFF_ONLY})
    assert visible_fields(me, me)["gmail"] == "t@gmail.com"


def test_two_unsaved_users_are_not_mistaken_for_the_same_person():
    # Both have id None; identifying "self" by id alone would leak everything.
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", gmail="t@gmail.com",
                visibility={"gmail": STAFF_ONLY})
    assert viewer.id is None and target.id is None
    assert "gmail" not in visible_fields(viewer, target)


def test_same_person_loaded_twice_counts_as_self(session):
    me = _u(role=Role.STUDENT, telegram_id=5, gmail="t@gmail.com",
            visibility={"gmail": STAFF_ONLY})
    session.add(me)
    session.commit()
    twin = User(id=me.id, role=Role.STUDENT, last_name=me.last_name,
                first_name=me.first_name, gmail=me.gmail,
                visibility=dict(me.visibility))
    assert visible_fields(twin, me)["gmail"] == "t@gmail.com"


def test_teacher_sees_full_set_across_cohorts_ignoring_staff_only():
    viewer = _u(role=Role.TEACHER, primary_cohort="9999")
    target = _u(role=Role.STUDENT, primary_cohort="2021", gmail="t@gmail.com",
                visibility={"gmail": STAFF_ONLY})
    assert visible_fields(viewer, target)["gmail"] == "t@gmail.com"


def test_admin_overrides_staff_only_configurable():
    viewer = _u(role=Role.ADMIN)
    target = _u(role=Role.STUDENT, primary_cohort="2021", gmail="t@gmail.com",
                visibility={"gmail": STAFF_ONLY})
    assert visible_fields(viewer, target)["gmail"] == "t@gmail.com"


def test_admin_sees_admin_only_fields():
    viewer = _u(role=Role.ADMIN)
    target = _u(role=Role.STUDENT, matriculation="30000001")
    assert visible_fields(viewer, target)["matriculation"] == "30000001"


def test_admin_sees_personal_admin_only_fields():
    viewer = _u(role=Role.ADMIN)
    target = _u(role=Role.STUDENT, birthday="2000-01-02",
                citizenship="RU", comment="note")
    fields = visible_fields(viewer, target)
    assert fields["birthday"] == "2000-01-02"
    assert fields["citizenship"] == "RU"
    assert fields["comment"] == "note"


def test_student_never_sees_personal_admin_only_fields():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", birthday="2000-01-02",
                citizenship="RU", comment="note")
    fields = visible_fields(viewer, target)
    assert "birthday" not in fields
    assert "citizenship" not in fields
    assert "comment" not in fields


def test_student_never_sees_admin_only():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", matriculation="30000001")
    assert "matriculation" not in visible_fields(viewer, target)


def test_owner_never_sees_their_own_admin_only_fields_are_not_promoted():
    # A student is not told hidden fields exist -- but they are their own row,
    # so the ADMIN_ONLY rule must beat the self rule.
    me = _u(role=Role.STUDENT, birthday="2000-01-02", matriculation="30000001")
    fields = visible_fields(me, me)
    assert "birthday" not in fields
    assert "matriculation" not in fields


def test_teacher_never_sees_admin_only():
    viewer = _u(role=Role.TEACHER, primary_cohort="9999")
    target = _u(role=Role.STUDENT, primary_cohort="2021", matriculation="30000001")
    assert "matriculation" not in visible_fields(viewer, target)


def test_unknown_field_name_is_a_programming_error():
    with pytest.raises(KeyError):
        level_of(_u(), "no_such_field")
