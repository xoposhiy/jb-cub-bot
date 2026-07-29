import pytest

from jbcub_bot.core.gradebook import (
    Column,
    GradebookRow,
    IgnoredColumn,
    MappingError,
    parse_gradebook,
    sheet_column_name,
)


TERM_ROW = ["", "", "", "Fall 2025", "", "", "Spring 2026", ""]
CATEGORY_ROW = [
    "", "", "", "Mandatory", "Mandatory", "Fall 2025", "Methods", "CSC Seminars"
]
LABEL_ROW = [
    "Status", "Last name", "First name", "Math", "CS 101\nTutorial",
    "Credits EARNED", "Physics", "",
]
ROWS = [
    TERM_ROW,
    CATEGORY_ROW,
    LABEL_ROW,
    ["Active", "Ivanov", "Ivan", "91%", "4.33", "", "pass", "IS, CL"],
    [],
    ["Departed", "Petrov", "Petr", "", "incomplete", "TC", "", ""],
]


def test_header_by_content_and_rows_after_nameless_row_survive():
    parsed = parse_gradebook(ROWS, "Last name", "First name")
    assert [row.last_name for row in parsed.rows] == ["Ivanov", "Petrov"]


def test_identity_columns_can_start_at_zero():
    rows = [
        ["", "", "Fall 2025"],
        ["", "", "Mandatory"],
        ["Last name", "First name", "Math"],
        ["Ivanov", "Ivan", "91%"],
    ]
    parsed = parse_gradebook(rows, "Last name", "First name")
    assert parsed.rows == [GradebookRow("Ivanov", "Ivan", {2: "91%"})]


def test_missing_header_names_expected_columns():
    with pytest.raises(MappingError) as error:
        parse_gradebook([["a", "b"]], "Last name", "First name")
    assert "Last name" in str(error.value) and "First name" in str(error.value)


def test_columns_report_only_named_non_metadata_columns_without_a_term():
    rows = [
        ["", "", "", "", "Fall 2025"],
        ["", "", "", "", "Mandatory"],
        [
            "Status",
            "Last name",
            "First name",
            "Credits Failed after make-up",
            "Math",
        ],
        ["Active", "Ivanov", "Ivan", "3", "91%"],
    ]

    parsed = parse_gradebook(rows, "Last name", "First name")

    assert parsed.ignored_columns == [
        IgnoredColumn(index=3, label="Credits Failed after make-up")
    ]
    assert parsed.columns == [
        Column(
            index=4,
            term="Fall 2025",
            category="Mandatory",
            label="Math",
        )
    ]


def test_sheet_column_name_uses_spreadsheet_letters():
    assert sheet_column_name(0) == "A"
    assert sheet_column_name(25) == "Z"
    assert sheet_column_name(26) == "AA"
    assert sheet_column_name(51) == "AZ"


def test_fixture_columns_resolve_bands_categories_and_labels():
    parsed = parse_gradebook(ROWS, "Last name", "First name")
    columns = {column.index: column for column in parsed.columns}
    assert set(columns) == {3, 4, 5, 6, 7}
    assert parsed.ignored_columns == []
    assert columns[4].term == "Fall 2025"
    assert columns[4].category == "Mandatory"
    assert columns[4].label == "CS 101 Tutorial"
    assert columns[5].category == ""
    assert columns[7].term == "Spring 2026"
    assert columns[7].label == "CSC Seminars"


def test_empty_cells_are_not_stored():
    parsed = parse_gradebook(ROWS, "Last name", "First name")
    ivanov, petrov = parsed.rows
    assert ivanov.cells == {3: "91%", 4: "4.33", 6: "pass", 7: "IS, CL"}
    assert petrov.cells == {4: "incomplete", 5: "TC"}
