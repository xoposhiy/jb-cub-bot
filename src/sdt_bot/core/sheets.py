import re

import yaml

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")


class MappingError(Exception):
    pass


def load_mapping(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def extract_sheet_id(link: str) -> str:
    match = _SHEET_ID_RE.search(link)
    if match:
        return match.group(1)
    return link.strip()


def parse_cohort_index(rows: list[list[str]]) -> list[dict]:
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    index = {col: i for i, col in enumerate(header)}
    if "Cohort" not in index or "Link" not in index:
        raise MappingError("Cohorts tab needs 'Cohort' and 'Link' columns")

    def cell(row, name):
        i = index.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    out = []
    for row in rows[1:]:
        cohort = cell(row, "Cohort")
        if not cohort:
            continue
        out.append({
            "cohort": cohort,
            "link": cell(row, "Link"),
            "mapping": cell(row, "Mapping") or f"{cohort}.yaml",
        })
    return out


def normalize_rows(rows: list[list[str]], mapping: dict) -> list[dict]:
    if not rows:
        return []
    header = rows[0]
    index = {col: i for i, col in enumerate(header)}
    for field, column in mapping.items():
        if column not in index:
            raise MappingError(f"column {column!r} for field {field!r} not found")
    out = []
    for row in rows[1:]:
        record = {}
        for field, column in mapping.items():
            i = index[column]
            record[field] = row[i] if i < len(row) else ""
        out.append(record)
    return out
