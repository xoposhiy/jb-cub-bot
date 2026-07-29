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
