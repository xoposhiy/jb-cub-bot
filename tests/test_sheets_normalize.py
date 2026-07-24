import pytest

from jbcub_bot.core.sheets import MappingError, load_mapping, normalize_rows


def test_load_mapping(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("matriculation: \"Matr\"\nlast_name: \"Last name\"\n",
                 encoding="utf-8")
    m = load_mapping(str(p))
    assert m == {"matriculation": "Matr", "last_name": "Last name"}


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


def test_normalize_rows_missing_column_raises():
    rows = [["Last name"], ["Ivanov"]]
    mapping = {"matriculation": "Matr", "last_name": "Last name"}
    with pytest.raises(MappingError):
        normalize_rows(rows, mapping)


def test_extract_sheet_id_from_url():
    from jbcub_bot.core.sheets import extract_sheet_id
    url = "https://docs.google.com/spreadsheets/d/1AbC-dEf_123/edit#gid=0"
    assert extract_sheet_id(url) == "1AbC-dEf_123"


def test_extract_sheet_id_passthrough_bare_id():
    from jbcub_bot.core.sheets import extract_sheet_id
    assert extract_sheet_id("  1AbC-dEf_123 ") == "1AbC-dEf_123"


def test_parse_cohort_index():
    from jbcub_bot.core.sheets import parse_cohort_index
    rows = [
        ["Cohort", "Link", "Mapping"],
        ["2024", "https://docs.google.com/spreadsheets/d/AAA/edit", "sdt-2025-2028.yaml"],
        ["2023", "BBB", ""],  # no mapping -> default
        ["", "ignored", ""],  # blank cohort skipped
    ]
    out = parse_cohort_index(rows)
    assert out == [
        {"cohort": "2024", "link": "https://docs.google.com/spreadsheets/d/AAA/edit",
         "mapping": "sdt-2025-2028.yaml"},
        {"cohort": "2023", "link": "BBB", "mapping": "2023.yaml"},
    ]


def test_parse_cohort_index_missing_columns_raises():
    from jbcub_bot.core.sheets import parse_cohort_index
    with pytest.raises(MappingError):
        parse_cohort_index([["Cohort"], ["2024"]])  # no Link column
