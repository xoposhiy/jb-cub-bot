import csv
import io

from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.export import (
    cohort_csv,
    cohort_google_contacts_csv,
    csv_filename,
    google_contacts_csv_filename,
)


def _person(**kw):
    base = dict(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                primary_cohort="2024", matriculation="30000001",
                telegram_id=42, handle_observed="ivanov",
                gmail="ivan@gmail.com", comment="on leave")
    return User(**(base | kw))


def _rows(viewer, people):
    text = cohort_csv(viewer, people).decode("utf-8-sig")
    return [line.split(",") for line in text.strip().split("\r\n")]


def _parsed_rows(viewer, people):
    # csv.reader rather than a naive split(","): a cell neutralized with a
    # leading apostrophe, or one holding a literal quote, must round-trip.
    text = cohort_csv(viewer, people).decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def test_teacher_gets_the_linking_keys_but_no_admin_only_field():
    header = _rows(User(last_name="T", role=Role.TEACHER), [_person()])[0]
    assert "matriculation" in header and "telegram_id" in header
    assert "comment" not in header


def test_admin_gets_the_admin_only_fields_too():
    header = _rows(User(last_name="A", role=Role.ADMIN), [_person()])[0]
    assert "comment" in header
    # The skip set (not a category, and not visible_fields) is what excludes
    # these two -- verified against an admin, for whom nothing else would.
    assert "departed_at" not in header and "source_link" not in header


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
    assert row[header.index("telegram")] == "ivanov"    # leading @ stripped
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


# --- formula injection: a cell must never open a spreadsheet formula --------

def test_a_formula_looking_status_is_neutralized():
    header, row = _parsed_rows(User(last_name="A", role=Role.ADMIN),
                               [_person(status_line='=HYPERLINK("x")')])
    assert row[header.index("status_line")] == '\'=HYPERLINK("x")'


def test_a_comment_starting_with_a_dash_is_neutralized():
    header, row = _parsed_rows(User(last_name="A", role=Role.ADMIN),
                               [_person(comment="- on leave")])
    assert row[header.index("comment")] == "'- on leave"


def test_the_telegram_cell_drops_the_at_sign_rather_than_escape_it():
    header, row = _parsed_rows(User(last_name="A", role=Role.ADMIN),
                               [_person(handle_observed="ivanov")])
    assert row[header.index("telegram")] == "ivanov"


def test_an_ordinary_value_is_untouched():
    header, row = _parsed_rows(User(last_name="A", role=Role.ADMIN), [_person()])
    assert row[header.index("first_name")] == "Ivan"


def test_a_provisional_person_keeps_the_matriculation_column_with_an_empty_cell():
    # The header comes from the keys visible_fields returned, so the column
    # survives for the cohort's real students -- and their cells with it.
    people = [_person(matriculation="TMP-A1B2", first_name="Nina",
                      last_name="Nova"),
              _person()]
    header, first, second = _parsed_rows(User(last_name="A", role=Role.ADMIN),
                                        people)
    assert "matriculation" in header
    assert first[header.index("matriculation")] == ""
    assert second[header.index("matriculation")] == "30000001"


# --- the Google Contacts CSV: a narrower, fixed-column sibling -------------

def _gc_rows(viewer, people):
    text = cohort_google_contacts_csv(viewer, people).decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def test_gc_header_is_fixed_regardless_of_which_fields_are_set():
    header = _gc_rows(User(last_name="A", role=Role.ADMIN), [_person()])[0]
    assert header == [
        "Name", "Given Name", "Family Name",
        "E-mail 1 - Type", "E-mail 1 - Value",
        "E-mail 2 - Type", "E-mail 2 - Value",
        "IM 1 - Service", "IM 1 - Value",
        "Organization 1 - Name",
    ]


def test_gc_drops_admin_only_and_staff_only_fields():
    # comment/matriculation/telegram_id have no column at all -- an address
    # book has no place for them, unlike the plain cohort_csv.
    header = _gc_rows(User(last_name="A", role=Role.ADMIN), [_person()])[0]
    for name in ("comment", "matriculation", "telegram_id", "role"):
        assert name not in header


def test_gc_name_splits_into_given_and_family():
    header, row = _gc_rows(User(last_name="A", role=Role.ADMIN), [_person()])
    assert row[header.index("Name")] == "Ivan Ivanov"
    assert row[header.index("Given Name")] == "Ivan"
    assert row[header.index("Family Name")] == "Ivanov"


def test_gc_gmail_leads_email_1_with_cubemail_as_email_2():
    people = [_person(gmail="ivan@gmail.com", cubemail="ivan@cub.edu")]
    header, row = _gc_rows(User(last_name="A", role=Role.ADMIN), people)
    assert row[header.index("E-mail 1 - Value")] == "ivan@gmail.com"
    assert row[header.index("E-mail 2 - Value")] == "ivan@cub.edu"


def test_gc_cubemail_fills_email_1_when_gmail_is_missing():
    # gmail leading means "prefer it", not "reserve the first slot for it" --
    # a missing gmail must not leave E-mail 1 empty while E-mail 2 holds
    # the only address on file.
    people = [_person(gmail=None, cubemail="ivan@cub.edu")]
    header, row = _gc_rows(User(last_name="A", role=Role.ADMIN), people)
    assert row[header.index("E-mail 1 - Value")] == "ivan@cub.edu"
    assert row[header.index("E-mail 2 - Value")] == ""


def test_gc_im_and_organization_come_from_telegram_and_cohort():
    header, row = _gc_rows(User(last_name="A", role=Role.ADMIN), [_person()])
    assert row[header.index("IM 1 - Service")] == "Telegram"
    assert row[header.index("IM 1 - Value")] == "ivanov"
    assert row[header.index("Organization 1 - Name")] == "2024"


def test_gc_no_people_is_a_header_free_empty_file():
    assert cohort_google_contacts_csv(User(last_name="A", role=Role.ADMIN), []) == b""


def test_gc_a_formula_looking_name_is_neutralized():
    people = [_person(first_name="=HYPERLINK(\"x\")")]
    header, row = _gc_rows(User(last_name="A", role=Role.ADMIN), people)
    assert row[header.index("Given Name")] == "'=HYPERLINK(\"x\")"


def test_gc_filename_survives_a_hand_typed_cohort_name():
    assert google_contacts_csv_filename("2024") == "cohort-2024-google-contacts.csv"
    assert (google_contacts_csv_filename("BSc 2024/25")
            == "cohort-BSc_2024_25-google-contacts.csv")
