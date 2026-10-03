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
            # Stripped: one cohort's "Artem " and another's "Artem" are the
            # same person, and every sync would otherwise flip between them.
            record[field] = row[i].strip() if i < len(row) else ""
        if _ends_the_roster(record):
            break
        if "handle_sheet" in record:
            record["handle_sheet"] = normalize_handle(record["handle_sheet"])
        out.append(record)
    return out


from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import delete, select

from jbcub_bot.core import identity
from jbcub_bot.core.models import Grade, Role, User
from jbcub_bot.features.directory.matching import fold


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
class SettleReport:
    """What `settle_absentees` did: dates written, and rows taken away."""

    marked: list[DepartedUser] = field(default_factory=list)
    removed: list[DepartedUser] = field(default_factory=list)


@dataclass
class ProvisionalReport:
    """One cohort's placeholder rows, as they stand after a sync settled.

    `replaced` is the good ending -- the university issued the number and the
    person is an ordinary student now. The two handle lists are the only signal
    a first-year who cannot get in will ever produce: the bot has no way to
    reach them and tell them their sheet handle is wrong.
    """

    profiles: int = 0
    replaced: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    shared_handles: list[DuplicateKey] = field(default_factory=list)
    without_handle: list[str] = field(default_factory=list)


@dataclass
class ReconcileReport:
    differences: list[FieldDifference] = field(default_factory=list)
    duplicates: list[DuplicateKey] = field(default_factory=list)


def record_key(record: dict, key: str = "matriculation") -> str:
    """The key a record identifies its person by, stripped; "" when it has none.

    Every reader of a record's key goes through this, so all of them agree on
    what "the same key" means. Untrimmed, a trailing space in a real number is
    a different key and gives one person two rows -- and then a roster this
    person *is* on reads as one they are missing from.
    """
    return str(record.get(key) or "").strip()


def assign_provisional_keys(records: list[dict], generate,
                            key: str = "matriculation") -> None:
    """Key every record the sheet left without a number, so none is skipped.

    A placeholder typed into the column counts as no number: keyed by it, the
    row would turn departed when the real number arrives instead of being
    replaced.

    `generate` is a parameter rather than a call in here, the way `today` is a
    parameter of `settle_absentees`: a test pins the keys instead of matching
    a pattern.
    """
    for record in records:
        if not identity.is_matriculation_number(record_key(record, key)):
            record[key] = generate()


def cohort_start(cohort: str) -> int:
    """The year a cohort started, read off its name; -1 when it has none."""
    match = re.search(r"\d{4}", cohort)
    return int(match.group()) if match else -1


def assign_cohorts(roster: list[tuple[str, list[dict]]],
                   key: str = "matriculation") -> None:
    """Give a person every cohort that names them, the newest one as primary.

    A student who moves from a bachelor cohort to a master cohort is on both
    rosters under one number. Each cohort's pass would claim the row in turn,
    and the cohort processed last would win -- so the other one would lose
    the person's grades and list line without a word.

    `roster` is `(cohort, records)` in index order; on equal years the later
    cohort wins.
    """
    named_by: dict[str, list[str]] = {}
    for cohort, records in roster:
        for record in records:
            if value := record_key(record, key):
                named_by.setdefault(value, []).append(cohort)
    for cohort, records in roster:
        for record in records:
            cohorts = named_by.get(record_key(record, key), [cohort])
            primary = max(reversed(cohorts), key=cohort_start)
            record["primary_cohort"] = primary
            record["past_cohorts"] = sorted(c for c in set(cohorts)
                                            if c != primary)


def upsert_users(session, records: list[dict], key: str = "matriculation") -> None:
    for record in records:
        key_value = record_key(record, key)
        if not key_value:
            continue
        user = session.scalar(
            select(User).where(getattr(User, key) == key_value)
        )
        if user is None:
            user = User(**{key: key_value})
            session.add(user)
        # Named by the roster again, so they are back: clearing the mark here
        # (rather than in settle_absentees) means a return is undone by the same
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


def settle_absentees(session, cohort: str, records: list[dict], today: str,
                     key: str = "matriculation") -> SettleReport:
    """Settle this cohort's members that `records` no longer names.

    A provisional row is removed, an ordinary one is marked departed. The row
    holds nothing Google Sheets does not hold, so deleting it loses nothing and
    the next `/sync` builds it again from the sheet; a departure date is the
    bot's own and has to survive.

    Scoped to `primary_cohort == cohort` deliberately: every other cohort's
    students and every Rights-only row (admins and teachers, keyed on their
    handle, with no cohort at all) are missing from these records too, and
    settling them would hide the program's own staff from everyone.

    A member with no `key` of their own is spared as well -- the roster is keyed
    on it, so a row that was never matched against the roster says nothing by
    being absent from it.

    `today` is a parameter, not a `date.today()` call, so the caller owns what
    "now" means and a test can pin it.

    An ordinary row that is already marked is left alone: the date says when
    the roster stopped naming them, which a later sync overwriting it would
    turn into "just now". A row already carrying a date is still selected
    though -- a provisional row that somehow acquired one would otherwise never
    be looked at again and would linger forever.

    Caller commits -- keeps multi-sheet /sync atomic.
    """
    present = {record_key(record, key) for record in records
               if record_key(record, key)}
    report = SettleReport()
    removed_ids: list[int] = []
    for user in session.scalars(
        select(User).where(User.primary_cohort == cohort)
    ).all():
        key_value = getattr(user, key)
        if not key_value or key_value in present:
            continue
        person = DepartedUser(matriculation=str(key_value),
                              full_name=user.full_name)
        if identity.is_provisional(user):
            removed_ids.append(user.id)
            report.removed.append(person)
        elif not user.departed_at:
            user.departed_at = today
            report.marked.append(person)
    if removed_ids:
        # The grades go with the row, and here rather than in the grades pass:
        # that pass is skipped whenever a Gradebook is broken. `users.id` is an
        # INTEGER PRIMARY KEY, so SQLite reuses it, and a left-behind
        # `grades.user_id` would attach to whoever gets the number next -- a
        # teacher shown someone else's grades.
        session.execute(delete(Grade).where(Grade.user_id.in_(removed_ids)))
        session.execute(delete(User).where(User.id.in_(removed_ids)))
    return report


def provisional_report(session, cohort: str,
                       removed: list[DepartedUser]) -> ProvisionalReport:
    """This cohort's placeholder rows once `settle_absentees` has run.

    Counted off the rows rather than the records, so a prefix typed into the
    sheet by hand counts too and a forgotten placeholder keeps being reported
    for as long as it exists.

    A removal whose person now holds a real number is the number arriving, not
    someone leaving. Names are what pairs the two: the invented key is gone
    with the row, and the sheet spells the person the same way in both syncs.
    """
    rows = session.scalars(
        select(User).where(User.primary_cohort == cohort)
    ).all()
    report = ProvisionalReport(removed=[item.full_name for item in removed])
    real_names = {fold(user.full_name) for user in rows
                  if not identity.is_provisional(user)}
    report.replaced = [item.full_name for item in removed
                       if fold(item.full_name) in real_names]
    handles = Counter()
    for user in rows:
        if not identity.is_provisional(user):
            continue
        report.profiles += 1
        if user.handle_sheet:
            handles[user.handle_sheet] += 1
        else:
            report.without_handle.append(user.full_name)
    report.shared_handles = [DuplicateKey(value=handle, rows=count)
                             for handle, count in handles.items() if count > 1]
    report.without_handle.sort()
    report.shared_handles.sort(key=lambda item: item.value)
    return report


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
    keys = [record_key(record, key) for record in records
            if record_key(record, key)]
    counts = Counter(keys)
    report.duplicates = [
        DuplicateKey(value=value, rows=count)
        for value, count in counts.items()
        if count > 1
    ]
    duplicate_values = {item.value for item in report.duplicates}

    for record in records:
        raw_key = record_key(record, key)
        if not raw_key or raw_key in duplicate_values:
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
                    key=raw_key,
                    field=label,
                    sheet_value=str(sheet_value),
                    profile_value=str(profile_value),
                ))
    return report
