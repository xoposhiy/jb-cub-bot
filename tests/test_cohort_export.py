from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.export import cohort_csv, csv_filename


def _person(**kw):
    base = dict(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                primary_cohort="2024", matriculation="30000001",
                telegram_id=42, handle_observed="ivanov",
                gmail="ivan@gmail.com", comment="on leave")
    return User(**(base | kw))


def _rows(viewer, people):
    text = cohort_csv(viewer, people).decode("utf-8-sig")
    return [line.split(",") for line in text.strip().split("\r\n")]


def test_teacher_gets_the_linking_keys_but_no_admin_only_field():
    header = _rows(User(last_name="T", role=Role.TEACHER), [_person()])[0]
    assert "matriculation" in header and "telegram_id" in header
    assert "comment" not in header
    assert "departed_at" not in header and "source_link" not in header


def test_admin_gets_the_admin_only_fields_too():
    header = _rows(User(last_name="A", role=Role.ADMIN), [_person()])[0]
    assert "comment" in header


def test_header_is_field_names_in_fields_order():
    header = _rows(User(last_name="A", role=Role.ADMIN), [_person()])[0]
    assert header[:4] == ["first_name", "last_name", "role", "primary_cohort"]


def test_a_two_source_field_is_one_column_holding_the_winner():
    people = [_person(github_self="mine", github_sheet="theirs")]
    header, row = _rows(User(last_name="A", role=Role.ADMIN), people)
    assert header.count("github") == 1
    assert row[header.index("github")] == "mine"


def test_values_are_flattened_and_a_missing_one_is_empty():
    people = [_person(gmail=None)]
    header, row = _rows(User(last_name="A", role=Role.ADMIN), people)
    assert row[header.index("role")] == "Student"      # the enum's value
    assert row[header.index("telegram")] == "@ivanov"
    assert row[header.index("telegram_id")] == "42"
    assert row[header.index("gmail")] == ""


def test_starts_with_a_bom_and_quotes_a_comma():
    data = cohort_csv(User(last_name="A", role=Role.ADMIN),
                      [_person(comment="left, then came back")])
    assert data.startswith(b"\xef\xbb\xbf")
    assert b'"left, then came back"' in data


def test_no_people_is_a_header_free_empty_file():
    assert cohort_csv(User(last_name="A", role=Role.ADMIN), []) == b""


def test_filename_survives_a_hand_typed_cohort_name():
    assert csv_filename("2024") == "cohort-2024.csv"
    assert csv_filename("BSc 2024/25") == "cohort-BSc_2024_25.csv"
