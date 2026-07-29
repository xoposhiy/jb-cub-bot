"""Pure parsing of a cohort's Gradebook tab: rows and lists in and out."""

from dataclasses import dataclass


class MappingError(Exception):
    pass


_HEADER_SEARCH_ROWS = 5


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def _flatten(text: str) -> str:
    return " ".join(text.split())


def _find_header_row(rows: list[list[str]], last_col: str, first_col: str) -> int:
    for index, row in enumerate(rows[:_HEADER_SEARCH_ROWS]):
        cells = {cell.strip() for cell in row}
        if last_col in cells and first_col in cells:
            return index
    raise MappingError(
        f"Gradebook header row not found: expected {last_col!r} and "
        f"{first_col!r} together in one of the first {_HEADER_SEARCH_ROWS} rows"
    )


def _find_identity_columns(
    label_row: list[str], last_col: str, first_col: str
) -> tuple[int, int]:
    last_index = first_index = None
    for index, cell in enumerate(label_row):
        stripped = cell.strip()
        if stripped == last_col:
            last_index = index
        elif stripped == first_col:
            first_index = index
    if last_index is None or first_index is None:
        raise MappingError(
            f"Gradebook header row is missing {last_col!r} or {first_col!r}"
        )
    return last_index, first_index


@dataclass(frozen=True)
class Column:
    index: int
    term: str
    category: str
    label: str


def _parse_columns(
    rows: list[list[str]], header_row: int
) -> tuple[list[Column], int]:
    term_row = rows[header_row - 2] if header_row >= 2 else []
    category_row = rows[header_row - 1] if header_row >= 1 else []
    label_row = rows[header_row]
    width = max(len(term_row), len(category_row), len(label_row))

    columns = []
    ignored = 0
    term_carry = ""
    category_carry = ""
    for index in range(width):
        term_cell = _cell(term_row, index)
        category_cell = _cell(category_row, index)
        label_cell = _flatten(_cell(label_row, index))

        if term_cell:
            term_carry = term_cell
            category_carry = ""
        term = term_carry or category_cell

        if category_cell:
            category_carry = category_cell
        category = category_carry
        if category == term:
            category = ""

        if not term:
            ignored += 1
            continue
        label = label_cell or category
        if not label:
            continue
        columns.append(Column(index, term, category, label))
    return columns, ignored


@dataclass(frozen=True)
class GradebookRow:
    last_name: str
    first_name: str
    cells: dict[int, str]


@dataclass(frozen=True)
class ParsedGradebook:
    columns: list[Column]
    rows: list[GradebookRow]
    ignored_columns: int


def parse_gradebook(
    rows: list[list[str]], last_name_column: str, first_name_column: str
) -> ParsedGradebook:
    """Turn raw Gradebook rows into columns and per-student cells."""
    header_row = _find_header_row(rows, last_name_column, first_name_column)
    columns, ignored = _parse_columns(rows, header_row)
    last_index, first_index = _find_identity_columns(
        rows[header_row], last_name_column, first_name_column
    )

    data_rows = []
    for raw in rows[header_row + 1 :]:
        last_name = _cell(raw, last_index)
        first_name = _cell(raw, first_index)
        if not last_name and not first_name:
            continue
        cells = {}
        for column in columns:
            value = _cell(raw, column.index)
            if value:
                cells[column.index] = value
        data_rows.append(GradebookRow(last_name, first_name, cells))

    return ParsedGradebook(columns, data_rows, ignored)
