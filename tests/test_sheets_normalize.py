import pytest

from sdt_bot.core.sheets import MappingError, load_mapping, normalize_rows


def test_load_mapping(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("matriculation: \"Matr\"\nname: \"Name\"\n", encoding="utf-8")
    m = load_mapping(str(p))
    assert m == {"matriculation": "Matr", "name": "Name"}


def test_normalize_rows_maps_by_header():
    rows = [
        ["Matr", "Name", "Telegram"],
        ["30000001", "Ivan Ivanov", "ivanov"],
    ]
    mapping = {"matriculation": "Matr", "name": "Name", "handle_sheet": "Telegram"}
    out = normalize_rows(rows, mapping)
    assert out == [
        {"matriculation": "30000001", "name": "Ivan Ivanov", "handle_sheet": "ivanov"}
    ]


def test_normalize_rows_missing_column_raises():
    rows = [["Name"], ["Ivan"]]
    mapping = {"matriculation": "Matr", "name": "Name"}
    with pytest.raises(MappingError):
        normalize_rows(rows, mapping)


def test_extract_sheet_id_from_url():
    from sdt_bot.core.sheets import extract_sheet_id
    url = "https://docs.google.com/spreadsheets/d/1AbC-dEf_123/edit#gid=0"
    assert extract_sheet_id(url) == "1AbC-dEf_123"


def test_extract_sheet_id_passthrough_bare_id():
    from sdt_bot.core.sheets import extract_sheet_id
    assert extract_sheet_id("  1AbC-dEf_123 ") == "1AbC-dEf_123"


def test_parse_cohort_index():
    from sdt_bot.core.sheets import parse_cohort_index
    rows = [
        ["Cohort", "Link", "Mapping"],
        ["2024", "https://docs.google.com/spreadsheets/d/AAA/edit", "cohort-2024.yaml"],
        ["2023", "BBB", ""],  # no mapping -> default
        ["", "ignored", ""],  # blank cohort skipped
    ]
    out = parse_cohort_index(rows)
    assert out == [
        {"cohort": "2024", "link": "https://docs.google.com/spreadsheets/d/AAA/edit",
         "mapping": "cohort-2024.yaml"},
        {"cohort": "2023", "link": "BBB", "mapping": "2023.yaml"},
    ]


def test_parse_cohort_index_missing_columns_raises():
    from sdt_bot.core.sheets import parse_cohort_index
    with pytest.raises(MappingError):
        parse_cohort_index([["Cohort"], ["2024"]])  # no Link column
