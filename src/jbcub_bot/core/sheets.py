import re

import yaml

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
_HANDLE_URL_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(@?[A-Za-z0-9_]+)", re.IGNORECASE
)


class MappingError(Exception):
    pass


def normalize_handle(value: str | None) -> str | None:
    """Normalize a Telegram handle to a bare username (no '@', no URL).

    Accepts '@name', 'name', or 't.me/name' / 'https://t.me/name'. Returns
    None for empty/blank input so handles are stored in a single canonical form.
    """
    if not value:
        return None
    handle = value.strip()
    match = _HANDLE_URL_RE.search(handle)
    if match:
        handle = match.group(1)
    handle = handle.lstrip("@").strip()
    return handle or None


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
        if "handle_sheet" in record:
            record["handle_sheet"] = normalize_handle(record["handle_sheet"])
        out.append(record)
    return out


from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select

from jbcub_bot.core.models import Role, User

SHEET_OWNED = (
    "last_name", "first_name", "handle_sheet", "gmail", "github", "codeforces",
    "birthday", "citizenship", "comment",
    "primary_cohort", "past_cohorts", "role",
)


def upsert_users(session, records: list[dict], key: str = "matriculation") -> None:
    for record in records:
        key_value = record.get(key)
        if not key_value:
            continue
        user = session.scalar(
            select(User).where(getattr(User, key) == key_value)
        )
        if user is None:
            user = User(**{key: key_value})
            session.add(user)
        for field_name in SHEET_OWNED:
            if field_name in record:
                value = record[field_name]
                if field_name == "role":
                    if not value:
                        continue  # blank role -> leave default/existing
                    value = Role(value)
                setattr(user, field_name, value)
    # Caller commits — keeps multi-sheet /sync atomic.


@dataclass
class ReconcileReport:
    drift: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)


def reconcile(session, records: list[dict], key: str = "matriculation") -> ReconcileReport:
    report = ReconcileReport()
    keys = [r.get(key) for r in records if r.get(key)]
    report.duplicates = [k for k, n in Counter(keys).items() if n > 1]
    for record in records:
        key_value = record.get(key)
        if not key_value:
            continue
        user = session.scalar(
            select(User).where(getattr(User, key) == key_value)
        )
        if user is None:
            report.unmatched.append(key_value)
            continue
        observed = user.handle_observed
        sheet_handle = record.get("handle_sheet")
        if observed and sheet_handle and observed != sheet_handle:
            report.drift.append(key_value)
    return report
