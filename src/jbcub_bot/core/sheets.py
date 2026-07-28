import difflib
import re

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
_HANDLE_URL_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(@?[A-Za-z0-9_]+)", re.IGNORECASE
)


class MappingError(Exception):
    pass


SHEET_OWNED = (
    "last_name", "first_name", "handle_sheet", "gmail",
    "github_sheet", "codeforces_sheet",
    "birthday", "citizenship", "comment",
    "primary_cohort", "past_cohorts", "role",
)

# Every field name a sheet header may use. `matriculation` is the student key,
# not a sheet-owned field. `cubemail` is accepted but has no User column yet, so
# it is read and dropped -- the sheets have named it since before this check.
KNOWN_FIELDS = frozenset(SHEET_OWNED + ("matriculation", "cubemail"))


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


def extract_sheet_id(link: str) -> str:
    match = _SHEET_ID_RE.search(link)
    if match:
        return match.group(1)
    return link.strip()


# The two Cohorts columns that describe the cohort itself. Every other column
# there is one of our field names.
COHORT_INDEX_COLUMNS = ("Cohort", "Link")


def _known_field(name: str) -> str:
    """Return `name` if it is one of our field names, else explain the typo.

    Header cells are hand-typed by admins, so a misspelling is the likeliest
    mistake -- and the most expensive one, since an unrecognized column would
    otherwise drop a whole field's data without a word.
    """
    if name in KNOWN_FIELDS:
        return name
    near = difflib.get_close_matches(name, sorted(KNOWN_FIELDS), n=1)
    hint = f" (did you mean {near[0]!r}?)" if near else ""
    raise MappingError(f"unknown field {name!r}{hint}")


def _require(mapping: dict, required, subject: str | None = None) -> None:
    missing = [f for f in required if f not in mapping]
    if missing:
        prefix = f"{subject}: " if subject else ""
        raise MappingError(
            prefix + "missing a column for "
            + ", ".join(repr(f) for f in missing)
        )


def parse_cohort_index(rows: list[list[str]]) -> list[dict]:
    """Read the Cohorts tab: one row per cohort, carrying its own field mapping.

    Past 'Cohort' and 'Link', each header cell names one of our fields and the
    cell beneath it names that field's column in the cohort's own sheet. That
    keeps the mapping next to the link it belongs to, editable by an admin.
    """
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    index = {col: i for i, col in enumerate(header)}
    if "Cohort" not in index or "Link" not in index:
        raise MappingError("Cohorts tab needs 'Cohort' and 'Link' columns")
    fields = [
        (i, _known_field(col))
        for i, col in enumerate(header)
        if col and col not in COHORT_INDEX_COLUMNS
    ]

    def cell(row, i):
        return row[i].strip() if i < len(row) else ""

    out = []
    for row in rows[1:]:
        cohort = cell(row, index["Cohort"])
        if not cohort:
            continue
        # A blank cell means this cohort's sheet has no such column.
        mapping = {field: cell(row, i) for i, field in fields if cell(row, i)}
        # upsert_users keys students on matriculation; without it every row of
        # the cohort is skipped and /sync reports success having written nothing.
        _require(mapping, ("matriculation",), f"cohort {cohort!r}")
        out.append({
            "cohort": cohort,
            "link": cell(row, index["Link"]),
            "mapping": mapping,
        })
    return out


def identity_mapping(header: list[str], required=()) -> dict:
    """Mapping for a tab whose columns already use our own field names.

    The Rights tab is ours to shape, so it skips the translation step and only
    has its header checked.
    """
    mapping = {}
    for col in header:
        col = col.strip()
        if col:
            mapping[_known_field(col)] = col
    _require(mapping, required)
    return mapping


def normalize_rows(rows: list[list[str]], mapping: dict) -> list[dict]:
    if not rows:
        return []
    header = rows[0]
    index = {col: i for i, col in enumerate(header)}
    for field, column in mapping.items():
        if column not in index:
            # The fix is to make the Cohorts cell match a real column, so name
            # the ones this sheet has rather than only the one it lacks.
            available = ", ".join(repr(c) for c in header if c.strip())
            raise MappingError(
                f"column {column!r} for field {field!r} not found; "
                f"this sheet has: {available or '(no named columns)'}"
            )
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


# Fields the roster and the bot can both hold a value for: (the record key the
# sheet fills, the column the bot fills, the profile field's name). The bot
# never resolves a disagreement itself -- an admin edits the sheet.
DRIFT_PAIRS = (
    ("handle_sheet", "handle_observed", "telegram"),
    ("github_sheet", "github_self", "github"),
    ("codeforces_sheet", "codeforces_self", "codeforces"),
)


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
        for sheet_key, own_column, label in DRIFT_PAIRS:
            sheet_value = record.get(sheet_key)
            own_value = getattr(user, own_column)
            if sheet_value and own_value and sheet_value != own_value:
                report.drift.append(f"{key_value}:{label}")
    return report
