"""Google Sheets as a read-only source of truth for the roster.

The bot never writes to a sheet. A roster field is the sheets' to own, and an
admin editing a sheet is how it changes; anything the bot owns
(`telegram_id`, `handle_observed`, `status_line`, `*_self`, `visibility`) must
survive re-import untouched. `matriculation` is the only stable student key.

A field a user can set therefore has **two** columns: `*_sheet`, listed in
`SHEET_OWNED`, and `*_self`, theirs. Nothing reconciles the two automatically --
`visibility.field_value` prefers the user's and shows the roster's beside it,
and `DRIFT_PAIRS` makes `/sync` report the disagreement for an admin to settle.

Which sheet column means which field is itself sheet data, not repo data: on the
`Cohorts` tab every column past `Cohort`/`Link` is one of our field names and
the cell beneath it is what that cohort calls it, so two cohorts may name the
same field differently and a blank cell means that cohort lacks it. The `Rights`
tab is ours to shape, so it maps to itself (`identity_mapping`). Both headers
are checked against `KNOWN_FIELDS` and an unknown name aborts `/sync` rather
than silently dropping a field a typo made unreadable. Adding a syncable field
means adding it to `SHEET_OWNED` -- there is no config file.
"""
import difflib
import re

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
_HANDLE_URL_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(@?[A-Za-z0-9_]+)", re.IGNORECASE
)


class MappingError(Exception):
    pass


SHEET_OWNED = (
    "last_name", "first_name", "handle_sheet", "gmail", "cubemail",
    "github_sheet", "codeforces_sheet",
    "birthday", "citizenship", "comment",
    "primary_cohort", "past_cohorts", "role", "source_link",
)

# Every field name a sheet header may use. `matriculation` is the student key,
# not a sheet-owned field.
KNOWN_FIELDS = frozenset(SHEET_OWNED + ("matriculation",))


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


def sheet_url(link: str) -> str:
    """Normalize a spreadsheet Link/id into a clickable URL."""
    link = (link or "").strip()
    if link.startswith("http://") or link.startswith("https://"):
        return link
    return f"https://docs.google.com/spreadsheets/d/{extract_sheet_id(link)}"


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


# A row identifies a person by name or by matriculation number. One that does
# neither is the blank separator a roster sheet puts between its current
# students and the expelled/transferred ones kept below for history -- so it
# ends the import rather than being skipped. Requiring *both* to be missing
# keeps a student still awaiting a matriculation number from cutting the
# roster short.
_ROSTER_IDENTITY = ("matriculation", "last_name", "first_name")


def _ends_the_roster(record: dict) -> bool:
    return not any(
        (record.get(field) or "").strip() for field in _ROSTER_IDENTITY
    )


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
        if _ends_the_roster(record):
            break
        if "handle_sheet" in record:
            record["handle_sheet"] = normalize_handle(record["handle_sheet"])
        out.append(record)
    return out


from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select

from jbcub_bot.core.models import Role, User


@dataclass(frozen=True)
class DuplicateKey:
    value: str
    rows: int


@dataclass(frozen=True)
class FieldDifference:
    key: str
    field: str
    sheet_value: str
    profile_value: str


@dataclass(frozen=True)
class DepartedUser:
    matriculation: str
    full_name: str


@dataclass
class ReconcileReport:
    differences: list[FieldDifference] = field(default_factory=list)
    duplicates: list[DuplicateKey] = field(default_factory=list)


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
        # Named by the roster again, so they are back: clearing the mark here
        # (rather than in mark_departed) means a return is undone by the same
        # pass that resumes updating their fields.
        user.departed_at = None
        for field_name in SHEET_OWNED:
            if field_name in record:
                value = record[field_name]
                if field_name == "role":
                    if not value:
                        continue  # blank role -> leave default/existing
                    value = Role(value)
                setattr(user, field_name, value)
    # Caller commits — keeps multi-sheet /sync atomic.


def mark_departed(session, cohort: str, records: list[dict], today: str,
                  key: str = "matriculation") -> list[DepartedUser]:
    """Mark this cohort's members that `records` no longer names and return them.

    Scoped to `primary_cohort == cohort` deliberately: every other cohort's
    students and every Rights-only row (admins and teachers, keyed on their
    handle, with no cohort at all) are missing from these records too, and
    marking them would hide the program's own staff from everyone.

    A member with no `key` of their own is spared as well -- the roster is keyed
    on it, so a row that was never matched against the roster says nothing by
    being absent from it.

    `today` is a parameter, not a `date.today()` call, so the caller owns what
    "now" means and a test can pin it.

    Already-marked rows are left alone: the date says when the roster stopped
    naming them, which a later sync overwriting it would turn into "just now".

    Caller commits -- keeps multi-sheet /sync atomic.
    """
    present = {r.get(key) for r in records if r.get(key)}
    stmt = select(User).where(
        User.primary_cohort == cohort, User.departed_at.is_(None)
    )
    marked: list[DepartedUser] = []
    for user in session.scalars(stmt).all():
        key_value = getattr(user, key)
        if key_value and key_value not in present:
            user.departed_at = today
            marked.append(DepartedUser(
                matriculation=str(key_value),
                full_name=user.full_name,
            ))
    return marked


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
    keys = [str(record.get(key)) for record in records if record.get(key)]
    counts = Counter(keys)
    report.duplicates = [
        DuplicateKey(value=value, rows=count)
        for value, count in counts.items()
        if count > 1
    ]
    duplicate_values = {item.value for item in report.duplicates}

    for record in records:
        raw_key = record.get(key)
        if not raw_key or str(raw_key) in duplicate_values:
            continue
        user = session.scalar(
            select(User).where(getattr(User, key) == raw_key)
        )
        if user is None:
            continue
        for sheet_key, own_column, label in DRIFT_PAIRS:
            sheet_value = record.get(sheet_key)
            profile_value = getattr(user, own_column)
            if (
                sheet_value
                and profile_value
                and sheet_value != profile_value
            ):
                report.differences.append(FieldDifference(
                    key=str(raw_key),
                    field=label,
                    sheet_value=str(sheet_value),
                    profile_value=str(profile_value),
                ))
    return report
