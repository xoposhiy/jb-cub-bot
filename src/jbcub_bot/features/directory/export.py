"""One cohort as a CSV, for matching these people in another system.

Pure: a viewer, a list of users, bytes out -- no aiogram, no session. The
columns are whatever `visible_fields` gave for the people in hand, so the
export can never show a field the profile screen would hide. Headers are field
names rather than labels, and values come back unmerged: a cell is read by a
machine, not by the person it belongs to.
"""

import csv
import io
import re

from jbcub_bot.core.models import User
from jbcub_bot.features.directory.visibility import FIELDS, visible_fields

# Neither says anything about the person: `source_link` names the spreadsheet
# and repeats in every row, and `departed_at` is empty in every row because
# /cohort lists only current people.
_SKIP = frozenset({"source_link", "departed_at"})

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def csv_filename(cohort: str) -> str:
    """A filename Telegram and a laptop both accept.

    A cohort name is a hand-typed sheet cell -- it may hold a space or a slash.
    """
    return f"cohort-{_UNSAFE.sub('_', cohort)}.csv"


def google_contacts_csv_filename(cohort: str) -> str:
    return f"cohort-{_UNSAFE.sub('_', cohort)}-google-contacts.csv"


def _cell(value) -> str:
    if value is None:
        return ""
    text = str(value.value) if hasattr(value, "value") else str(value)  # enum -> its value
    # A spreadsheet evaluates a cell that opens with = + - or @ as a formula,
    # and status_line/comment/citizenship are free text a person or an admin
    # typed -- one of them starting with `=HYPERLINK(...)` would exfiltrate the
    # neighbouring cells when this file is opened. `@` only ever leads the
    # `telegram` cell's handle, and the bare handle is what another system
    # matches on anyway, so drop it outright rather than merely escaping it.
    text = text.removeprefix("@")
    if text[:1] in ("=", "+", "-"):
        text = f"'{text}"
    return text


def cohort_csv(viewer: User, people: list[User]) -> bytes:
    """UTF-8-with-BOM CSV of `people` as `viewer` may see them.

    The header is the union of the keys `visible_fields` returned, in FIELDS
    order -- taken from the rows rather than from the field table so the two
    can never disagree. No people means no header either: an export of nobody
    is an empty file, not a promise of columns.
    """
    rows = [visible_fields(viewer, person, merged=False) for person in people]
    present = {name for row in rows for name in row}
    header = [spec.name for spec in FIELDS
              if spec.name in present and spec.name not in _SKIP]
    if not header:
        return b""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(row.get(name)) for name in header])
    # utf-8-sig: `comment` and `citizenship` are free text an admin typed, and
    # Excel mojibakes a plain UTF-8 CSV.
    return buffer.getvalue().encode("utf-8-sig")


_GOOGLE_CONTACTS_HEADER = [
    "Name", "Given Name", "Family Name",
    "E-mail 1 - Type", "E-mail 1 - Value",
    "E-mail 2 - Type", "E-mail 2 - Value",
    "IM 1 - Service", "IM 1 - Value",
    "Organization 1 - Name",
]


def cohort_google_contacts_csv(viewer: User, people: list[User]) -> bytes:
    """A cohort as a CSV Google Contacts can import.

    Fixed columns, not the profile's -- an address book wants a name, a way to
    reach someone, and which cohort they're in, not `matriculation` or
    `comment`. `gmail` leads E-mail 1 over `cubemail`: it is the address most
    likely to already be the person's real Google account, so an import lands
    on the contact Google itself would have suggested.
    """
    if not people:
        return b""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_GOOGLE_CONTACTS_HEADER)
    for person in people:
        fields = visible_fields(viewer, person, merged=False)
        first = _cell(fields.get("first_name"))
        last = _cell(fields.get("last_name"))
        gmail = fields.get("gmail")
        cubemail = fields.get("cubemail")
        telegram = fields.get("telegram")
        # gmail always leads: if it's missing, cubemail fills E-mail 1 rather
        # than leaving it empty with the address stranded in E-mail 2.
        primary, primary_type = (gmail, "Home") if gmail else (cubemail, "Work")
        secondary, secondary_type = (cubemail, "Work") if gmail and cubemail else (None, "")
        writer.writerow([
            f"{first} {last}".strip(),
            first,
            last,
            primary_type if primary else "",
            _cell(primary),
            secondary_type if secondary else "",
            _cell(secondary),
            "Telegram" if telegram else "",
            _cell(telegram),
            _cell(fields.get("primary_cohort")),
        ])
    return buffer.getvalue().encode("utf-8-sig")
