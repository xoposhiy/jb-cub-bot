import pytest

from jbcub_bot.core.gradebook import GradebookRow, MappingError, parse_gradebook


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


def test_columns_resolve_bands_categories_labels_and_ignored_count():
    parsed = parse_gradebook(ROWS, "Last name", "First name")
    columns = {column.index: column for column in parsed.columns}
    assert set(columns) == {3, 4, 5, 6, 7}
    assert parsed.ignored_columns == 3
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
