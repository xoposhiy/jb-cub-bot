import pytest

from jbcub_bot.core.sheets import MappingError, normalize_rows


def test_normalize_rows_maps_by_header():
    rows = [
        ["Matr", "Last name", "First name", "Telegram"],
        ["30000001", "Ivanov", "Ivan", "ivanov"],
    ]
    mapping = {"matriculation": "Matr", "last_name": "Last name",
               "first_name": "First name", "handle_sheet": "Telegram"}
    out = normalize_rows(rows, mapping)
    assert out == [
        {"matriculation": "30000001", "last_name": "Ivanov",
         "first_name": "Ivan", "handle_sheet": "ivanov"}
    ]


def test_normalize_handle_strips_at_url_and_whitespace():
    from jbcub_bot.core.sheets import normalize_handle
    assert normalize_handle("@xoposhiy") == "xoposhiy"
    assert normalize_handle("xoposhiy") == "xoposhiy"
    assert normalize_handle("  @xoposhiy ") == "xoposhiy"
    assert normalize_handle("https://t.me/xoposhiy") == "xoposhiy"
    assert normalize_handle("t.me/xoposhiy") == "xoposhiy"
    assert normalize_handle("https://telegram.me/@xoposhiy") == "xoposhiy"
    assert normalize_handle("") is None
    assert normalize_handle(None) is None
    assert normalize_handle("   ") is None


def test_normalize_rows_normalizes_handle():
    rows = [
        ["Last name", "Telegram"],
        ["Ivanov", "https://t.me/@ivanov"],
    ]
    mapping = {"last_name": "Last name", "handle_sheet": "Telegram"}
    out = normalize_rows(rows, mapping)
    assert out == [{"last_name": "Ivanov", "handle_sheet": "ivanov"}]


def test_normalize_rows_stops_at_the_first_row_with_neither_name_nor_matriculation():
    # A roster sheet ends at a blank separator row; below it sit expelled and
    # transferred students, 'del' markers and 'Total:' tallies. Reading past the
    # break would re-import people who left.
    rows = [
        ["Matr", "Last name", "First name"],
        ["30000001", "Ivanov", "Ivan"],
        ["30000002", "Yurttas", "Mert"],
        [],                                   # the break
        ["", "", ""],
        ["30000009", "Expelled", "Eve"],      # below the break: ignored
    ]
    mapping = {"matriculation": "Matr", "last_name": "Last name",
               "first_name": "First name"}
    out = normalize_rows(rows, mapping)
    assert [r["last_name"] for r in out] == ["Ivanov", "Yurttas"]


def test_normalize_rows_stops_on_a_separator_row_that_still_has_other_cells():
    # The break is not always an empty row -- 'Total:  31' and '@handle  del'
    # rows carry cells but no name and no matriculation.
    rows = [
        ["Matr", "Last name", "First name", "Comment"],
        ["30000001", "Ivanov", "Ivan", ""],
        ["", "", "", "Total: 1"],
        ["30000009", "Expelled", "Eve", ""],
    ]
    mapping = {"matriculation": "Matr", "last_name": "Last name",
               "first_name": "First name", "comment": "Comment"}
    assert [r["last_name"] for r in normalize_rows(rows, mapping)] == ["Ivanov"]


def test_normalize_rows_keeps_a_student_whose_matriculation_is_not_assigned_yet():
    # Only a row missing *both* ends the roster: a student still waiting for a
    # matriculation number must not truncate everyone below them.
    rows = [
        ["Matr", "Last name", "First name"],
        ["", "Nomatric", "Nina"],
        ["30000002", "Petrov", "Pyotr"],
    ]
    mapping = {"matriculation": "Matr", "last_name": "Last name",
               "first_name": "First name"}
    out = normalize_rows(rows, mapping)
    assert [r["last_name"] for r in out] == ["Nomatric", "Petrov"]


def test_normalize_rows_keeps_a_row_with_a_matriculation_but_no_name():
    rows = [
        ["Matr", "Last name", "First name"],
        ["30000001", "", ""],
        ["30000002", "Petrov", "Pyotr"],
    ]
    mapping = {"matriculation": "Matr", "last_name": "Last name",
               "first_name": "First name"}
    out = normalize_rows(rows, mapping)
    assert [r["matriculation"] for r in out] == ["30000001", "30000002"]


def test_normalize_rows_missing_column_raises():
    rows = [["Last name"], ["Ivanov"]]
    mapping = {"matriculation": "Matr", "last_name": "Last name"}
    with pytest.raises(MappingError):
        normalize_rows(rows, mapping)


def test_normalize_rows_missing_column_error_lists_the_columns_the_sheet_has():
    # The fix is always "make the Cohorts cell match a real column", so the
    # error has to show which columns the cohort sheet actually offers.
    rows = [[" ", "First name", "Telegram"], ["Ivanov", "Ivan", "ivanov"]]
    mapping = {"last_name": "Last name"}
    with pytest.raises(MappingError) as err:
        normalize_rows(rows, mapping)
    assert "last_name" in str(err.value)
    assert "'Last name'" in str(err.value)
    assert "First name" in str(err.value)


def test_extract_sheet_id_from_url():
    from jbcub_bot.core.sheets import extract_sheet_id
    url = "https://docs.google.com/spreadsheets/d/1AbC-dEf_123/edit#gid=0"
    assert extract_sheet_id(url) == "1AbC-dEf_123"


def test_extract_sheet_id_passthrough_bare_id():
    from jbcub_bot.core.sheets import extract_sheet_id
    assert extract_sheet_id("  1AbC-dEf_123 ") == "1AbC-dEf_123"


def test_parse_cohort_index_reads_each_cohorts_mapping_from_its_row():
    # Every column past Cohort/Link is one of our field names; the cell under it
    # is what that field is called in that cohort's own sheet. Two cohorts can
    # name the same field differently.
    from jbcub_bot.core.sheets import parse_cohort_index
    rows = [
        ["Cohort", "Link", "matriculation", "last_name", "handle_sheet"],
        ["2024", "https://docs.google.com/spreadsheets/d/AAA/edit",
         "Matriculation Num.", "Last name", "Telegram"],
        ["2023", "BBB", "Matr", "Surname", "TG"],
        ["", "ignored", "x", "y", "z"],  # blank cohort skipped
    ]
    assert parse_cohort_index(rows) == [
        {"cohort": "2024",
         "link": "https://docs.google.com/spreadsheets/d/AAA/edit",
         "mapping": {"matriculation": "Matriculation Num.",
                     "last_name": "Last name", "handle_sheet": "Telegram"}},
        {"cohort": "2023", "link": "BBB",
         "mapping": {"matriculation": "Matr", "last_name": "Surname",
                     "handle_sheet": "TG"}},
    ]


def test_parse_cohort_index_treats_a_blank_cell_as_a_field_that_cohort_lacks():
    # A cohort sheet with no Citizenship column leaves the cell empty rather
    # than needing a separate mapping. A short row means the same thing.
    from jbcub_bot.core.sheets import parse_cohort_index
    rows = [
        ["Cohort", "Link", "matriculation", "citizenship", "comment"],
        ["2024", "AAA", "Matr", "", "Comment"],
        ["2023", "BBB", "Matr"],  # row ends early
    ]
    out = parse_cohort_index(rows)
    assert out[0]["mapping"] == {"matriculation": "Matr", "comment": "Comment"}
    assert out[1]["mapping"] == {"matriculation": "Matr"}


def test_parse_cohort_index_rejects_an_unknown_field_name():
    # A typo in the header would otherwise silently drop a whole column of data.
    from jbcub_bot.core.sheets import parse_cohort_index
    rows = [
        ["Cohort", "Link", "matriculation", "last_nmae"],
        ["2024", "AAA", "Matr", "Last name"],
    ]
    with pytest.raises(MappingError) as err:
        parse_cohort_index(rows)
    assert "last_nmae" in str(err.value)
    assert "last_name" in str(err.value)  # suggests the near miss


def test_parse_cohort_index_requires_matriculation():
    # upsert_users keys students on matriculation: without it every row is
    # silently skipped and the sync reports success having written nothing.
    from jbcub_bot.core.sheets import parse_cohort_index
    rows = [
        ["Cohort", "Link", "last_name"],
        ["2024", "AAA", "Last name"],
    ]
    with pytest.raises(MappingError) as err:
        parse_cohort_index(rows)
    assert "matriculation" in str(err.value)


def test_parse_cohort_index_missing_columns_raises():
    from jbcub_bot.core.sheets import parse_cohort_index
    with pytest.raises(MappingError):
        parse_cohort_index([["Cohort"], ["2024"]])  # no Link column


def test_identity_mapping_maps_each_header_field_to_itself():
    # The Rights tab names its columns with our own field names, so it needs no
    # translation -- only a check that we recognize every one of them.
    from jbcub_bot.core.sheets import identity_mapping
    header = ["first_name", "last_name", "handle_sheet", "role", ""]
    assert identity_mapping(header) == {
        "first_name": "first_name", "last_name": "last_name",
        "handle_sheet": "handle_sheet", "role": "role",
    }


def test_identity_mapping_rejects_an_unknown_field_name():
    from jbcub_bot.core.sheets import identity_mapping
    with pytest.raises(MappingError) as err:
        identity_mapping(["first_name", "rolle"])
    assert "rolle" in str(err.value)
    assert "role" in str(err.value)


def test_identity_mapping_enforces_required_fields():
    from jbcub_bot.core.sheets import identity_mapping
    with pytest.raises(MappingError) as err:
        identity_mapping(["first_name", "role"], required=("handle_sheet",))
    assert "handle_sheet" in str(err.value)
