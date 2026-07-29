# Staff /cohort with a CSV export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/cohort` lets staff pick any cohort and answers with the list plus a CSV of the fields an external system needs; a student's `/cohort` is unchanged.

**Architecture:** `Category.STAFF` in `visibility.FIELDS` opens `matriculation` and `telegram_id` to teachers, and `visible_fields(..., merged=False)` yields machine-readable values. A new pure module `export.py` turns a viewer plus a list of users into CSV bytes. `/cohort` moves out of `handlers.py` into `cohort.py` with its own router, where it gains a cohort picker and its callback.

**Tech Stack:** Python 3.13, aiogram 3, SQLAlchemy 2, pytest (`uv run pytest`), stdlib `csv`/`io`/`re`.

**Design:** `docs/superpowers/specs/2026-07-29-cohort-export-design.md`

## Global Constraints

- Profile reads go through `features/directory/visibility.py`; no module may re-derive who sees what.
- `/cohort` lists only current people for every role — no `include_departed` anywhere in this feature.
- CSV headers are field names (`first_name`), never labels; skipped fields are exactly `source_link` and `departed_at`.
- CSV bytes are `utf-8-sig` (BOM), written by `csv.writer` with its default `\r\n`.
- The bot never writes to a Google Sheet.
- Run tests with `uv run pytest`.

---

### Task 1: `Category.STAFF` and unmerged field values

**Files:**
- Modify: `src/jbcub_bot/features/directory/visibility.py:16-21` (enum), `:59-72` (FIELDS), `:107-129` (`field_value`), `:175-199` (`visible_fields`)
- Test: `tests/test_visibility.py`

**Interfaces:**
- Produces: `visibility.Category.STAFF`; `visibility.field_value(user, name, *, merged: bool = True)`; `visibility.visible_fields(viewer, target, *, merged: bool = True) -> dict`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_visibility.py` (the file's `_u` helper builds a `User`):

```python
def test_teacher_sees_the_staff_fields_and_a_student_does_not():
    target = _u(role=Role.STUDENT, primary_cohort="2021",
                matriculation="30000001", telegram_id=42)
    teacher = visible_fields(_u(role=Role.TEACHER, primary_cohort="9999"), target)
    assert teacher["matriculation"] == "30000001"
    assert teacher["telegram_id"] == 42
    mate = visible_fields(_u(role=Role.STUDENT, primary_cohort="2021"), target)
    assert "matriculation" not in mate and "telegram_id" not in mate


def test_owner_is_not_shown_the_staff_fields():
    # A student may not learn their own telegram_id from the bot: STAFF is not
    # "everyone above me plus me".
    target = _u(role=Role.STUDENT, matriculation="30000001", telegram_id=42)
    assert "matriculation" not in visible_fields(target, target)


def test_unmerged_value_drops_the_roster_note():
    target = _u(role=Role.STUDENT, github_self="mine", github_sheet="theirs")
    assert visibility.field_value(target, "github") == "mine (roster: theirs)"
    assert visibility.field_value(target, "github", merged=False) == "mine"
    admin = _u(role=Role.ADMIN)
    assert visible_fields(admin, target, merged=False)["github"] == "mine"
```

Change `test_teacher_never_sees_admin_only` (`tests/test_visibility.py:336`) to a field that stays admin-only:

```python
def test_teacher_never_sees_admin_only():
    viewer = _u(role=Role.TEACHER, primary_cohort="9999")
    target = _u(role=Role.STUDENT, primary_cohort="2021", comment="left early")
    assert "comment" not in visible_fields(viewer, target)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_visibility.py -v`
Expected: FAIL — `AttributeError`/`KeyError` on `matriculation`, and `field_value()` got an unexpected keyword `merged`.

- [ ] **Step 3: Implement**

In `visibility.py`, add the category (keep the comment style of its neighbours):

```python
class Category(enum.Enum):
    ALWAYS = "always"              # unhideable: every linked user sees it
    CONFIGURABLE = "configurable"  # the owner chooses who sees it
    STAFF = "staff"                # admins and teachers -- not the owner
    ADMIN_ONLY = "admin_only"      # admins only -- the owner is not told it exists
```

Move the two fields in `FIELDS`:

```python
    FieldSpec("telegram_id", "Telegram ID", Category.STAFF),
...
    FieldSpec("matriculation", "Matriculation", Category.STAFF),
```

Thread `merged` through `field_value` — only the drift note is conditional:

```python
def field_value(user: User, name: str, *, merged: bool = True):
    """...

    `merged=False` drops the "(roster: …)" note and returns the winning value
    alone: an export cell is read by a machine, and /sync is what reports a
    disagreement.
    """
    if name == "telegram":
        handle = user.handle_observed or user.handle_sheet
        return f"@{handle}" if handle else None
    spec = BY_NAME[name]
    if spec.sources:
        own, roster = (getattr(user, column) or None for column in spec.sources)
        if merged and own and roster and own != roster:
            return f"{own} ({ROSTER_NOTE}: {roster})"
        return own or roster
    return getattr(user, name)
```

Add the gate and pass `merged` on in `visible_fields`:

```python
def visible_fields(viewer: User, target: User, *, merged: bool = True) -> dict:
    """... (docstring unchanged)"""
    is_admin = viewer.role is Role.ADMIN
    staff = is_staff(viewer)
    own = _is_self(viewer, target)
    mates = are_cohort_mates(viewer, target)

    fields: dict = {}
    for spec in FIELDS:
        if spec.category is Category.ADMIN_ONLY:
            if not is_admin:
                continue
        elif spec.category is Category.STAFF:
            # Not `or own`: these are the keys another system matches people
            # on, and their owner is deliberately not shown them.
            if not staff:
                continue
        elif spec.category is Category.CONFIGURABLE and not (own or staff):
            # Levels govern student-to-student visibility only; staff and the
            # owner are past this gate already.
            level = level_of(target, spec.name)
            if level == STAFF_ONLY:
                continue
            if level == COHORT and not mates:
                continue
        fields[spec.name] = field_value(target, spec.name, merged=merged)
    return fields
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. If `tests/test_privacy*.py` fails, the privacy screen is listing more than `CONFIGURABLE_FIELDS` — fix the screen, not the test.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/visibility.py tests/test_visibility.py
git commit -m "feat: teachers see matriculation and telegram_id"
```

---

### Task 2: `list_cohort_names`

**Files:**
- Modify: `src/jbcub_bot/features/directory/search.py:43-46`
- Test: `tests/test_directory_search.py`

**Interfaces:**
- Produces: `search.list_cohort_names(session) -> list[str]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_directory_search.py`:

```python
def test_list_cohort_names_newest_first_and_only_where_someone_is_current(session):
    session.add_all([
        User(first_name="A", last_name="One", primary_cohort="2023"),
        User(first_name="B", last_name="Two", primary_cohort="2024"),
        User(first_name="C", last_name="Three", primary_cohort="2024"),
        User(first_name="D", last_name="Gone", primary_cohort="2019",
             departed_at="2026-07-28"),
        User(first_name="E", last_name="Staff", role=Role.ADMIN),
    ])
    session.commit()
    assert list_cohort_names(session) == ["2024", "2023"]
```

Add `list_cohort_names` to that file's import from `jbcub_bot.features.directory.search`, and `Role` to its `models` import if it is not there.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_directory_search.py -v`
Expected: FAIL — `ImportError: cannot import name 'list_cohort_names'`.

- [ ] **Step 3: Implement**

Append to `search.py`:

```python
def list_cohort_names(session) -> list[str]:
    """Every cohort that still has a current member, newest first.

    No `include_departed`: a cohort whose last member left is not a cohort to
    offer. The names are years, so reverse-alphabetical is chronological
    without parsing one.
    """
    stmt = select(User.primary_cohort).where(
        User.primary_cohort.is_not(None), User.departed_at.is_(None)
    ).distinct()
    return sorted(session.scalars(stmt).all(), reverse=True)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_directory_search.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/search.py tests/test_directory_search.py
git commit -m "feat: list the cohorts that have a current member"
```

---

### Task 3: the CSV exporter

**Files:**
- Create: `src/jbcub_bot/features/directory/export.py`
- Test: `tests/test_cohort_export.py`

**Interfaces:**
- Consumes: `visibility.visible_fields(viewer, target, *, merged=False)` (Task 1)
- Produces: `export.cohort_csv(viewer: User, people: list[User]) -> bytes`; `export.csv_filename(cohort: str) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cohort_export.py`:

```python
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.export import cohort_csv, csv_filename


def _person(**kw):
    base = dict(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                primary_cohort="2024", matriculation="30000001",
                telegram_id=42, handle_observed="ivanov",
                gmail="ivan@gmail.com", comment="on leave")
    return User(**(base | kw))


def _rows(viewer, people):
    text = cohort_csv(viewer, people).decode("utf-8-sig")
    return [line.split(",") for line in text.strip().split("\r\n")]


def test_teacher_gets_the_linking_keys_but_no_admin_only_field():
    header = _rows(User(last_name="T", role=Role.TEACHER), [_person()])[0]
    assert "matriculation" in header and "telegram_id" in header
    assert "comment" not in header
    assert "departed_at" not in header and "source_link" not in header


def test_admin_gets_the_admin_only_fields_too():
    header = _rows(User(last_name="A", role=Role.ADMIN), [_person()])[0]
    assert "comment" in header


def test_header_is_field_names_in_fields_order():
    header = _rows(User(last_name="A", role=Role.ADMIN), [_person()])[0]
    assert header[:4] == ["first_name", "last_name", "role", "primary_cohort"]


def test_a_two_source_field_is_one_column_holding_the_winner():
    people = [_person(github_self="mine", github_sheet="theirs")]
    header, row = _rows(User(last_name="A", role=Role.ADMIN), people)
    assert header.count("github") == 1
    assert row[header.index("github")] == "mine"


def test_values_are_flattened_and_a_missing_one_is_empty():
    people = [_person(gmail=None)]
    header, row = _rows(User(last_name="A", role=Role.ADMIN), people)
    assert row[header.index("role")] == "Student"      # the enum's value
    assert row[header.index("telegram")] == "@ivanov"
    assert row[header.index("telegram_id")] == "42"
    assert row[header.index("gmail")] == ""


def test_starts_with_a_bom_and_quotes_a_comma():
    data = cohort_csv(User(last_name="A", role=Role.ADMIN),
                      [_person(comment="left, then came back")])
    assert data.startswith(b"\xef\xbb\xbf")
    assert b'"left, then came back"' in data


def test_no_people_is_a_header_free_empty_file():
    assert cohort_csv(User(last_name="A", role=Role.ADMIN), []) == b""


def test_filename_survives_a_hand_typed_cohort_name():
    assert csv_filename("2024") == "cohort-2024.csv"
    assert csv_filename("BSc 2024/25") == "cohort-BSc_2024_25.csv"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_cohort_export.py -v`
Expected: FAIL — `ModuleNotFoundError: ... directory.export`.

- [ ] **Step 3: Implement**

Create `src/jbcub_bot/features/directory/export.py`:

```python
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
    if hasattr(value, "value"):  # enum -> its value
        return str(value.value)
    return str(value)


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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cohort_export.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/export.py tests/test_cohort_export.py
git commit -m "feat: build a cohort CSV from the fields a viewer may see"
```

---

### Task 4: the command, the picker and its callback

**Files:**
- Create: `src/jbcub_bot/features/directory/cohort.py`
- Modify: `src/jbcub_bot/features/directory/handlers.py:83-90` (delete `cmd_cohort`), `:21-32` (drop the now-unused `render_cohort_list` and `list_cohort` imports), `src/jbcub_bot/features/directory/__init__.py`
- Test: `tests/test_directory_cohort.py`, `tests/test_directory_handlers.py:5-10,68-72`

**Interfaces:**
- Consumes: `export.cohort_csv`, `export.csv_filename` (Task 3); `search.list_cohort_names` (Task 2); `search.list_cohort`; `render.render_cohort_list`
- Produces: `cohort.cmd` (its `specs` go in the manifest), `cohort.router`, `cohort.cmd_cohort`, `cohort.cb_pick`, `cohort.PICK_PREFIX`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_directory_cohort.py` (which today holds only render tests):

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jbcub_bot.features.directory.cohort import PICK_PREFIX, cb_pick, cmd_cohort


def _seed(session):
    session.add_all([
        User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
             primary_cohort="2024", matriculation="30000001"),
        User(first_name="Eve", last_name="Expelled", role=Role.STUDENT,
             primary_cohort="2024", matriculation="30000009",
             departed_at="2026-07-28"),
        User(first_name="Old", last_name="Timer", role=Role.STUDENT,
             primary_cohort="2023", matriculation="30000002"),
    ])
    session.commit()


def _msg():
    return SimpleNamespace(answer=AsyncMock(), answer_document=AsyncMock())


def _args(text):
    return SimpleNamespace(args=text)


async def test_a_student_still_gets_one_message_and_no_file(session):
    _seed(session)
    msg = _msg()
    viewer = User(first_name="V", last_name="Viewer", role=Role.STUDENT,
                  primary_cohort="2024")
    await cmd_cohort(msg, principal=viewer, session=session,
                     command=_args("2023"))
    assert "Ivan Ivanov" in msg.answer.await_args.args[0]
    assert "Old Timer" not in msg.answer.await_args.args[0]  # argument ignored
    msg.answer_document.assert_not_awaited()


async def test_staff_with_no_argument_get_a_button_per_cohort(session):
    _seed(session)
    msg = _msg()
    await cmd_cohort(msg, principal=User(last_name="T", role=Role.TEACHER),
                     session=session, command=_args(None))
    keyboard = msg.answer.await_args.kwargs["reply_markup"]
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == ["2024", "2023"]
    payloads = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert payloads[0] == f"{PICK_PREFIX}2024"
    msg.answer_document.assert_not_awaited()


async def test_staff_with_an_argument_get_the_list_and_one_document(session):
    _seed(session)
    msg = _msg()
    await cmd_cohort(msg, principal=User(last_name="A", role=Role.ADMIN),
                     session=session, command=_args(" 2024 "))
    text = msg.answer.await_args.args[0]
    assert "2024" in text and "Ivan Ivanov" in text
    assert "Expelled" not in text  # even for an admin
    document = msg.answer_document.await_args.args[0]
    assert document.filename == "cohort-2024.csv"
    assert b"Expelled" not in document.data
    assert b"Ivanov" in document.data


async def test_an_unknown_cohort_redraws_the_picker_with_a_note(session):
    _seed(session)
    msg = _msg()
    await cmd_cohort(msg, principal=User(last_name="A", role=Role.ADMIN),
                     session=session, command=_args("2019"))
    assert "2019" in msg.answer.await_args.args[0]
    assert msg.answer.await_args.kwargs["reply_markup"] is not None
    msg.answer_document.assert_not_awaited()


async def test_staff_are_told_when_there_are_no_cohorts_at_all(session):
    msg = _msg()
    await cmd_cohort(msg, principal=User(last_name="A", role=Role.ADMIN),
                     session=session, command=_args(None))
    assert "/sync" in msg.answer.await_args.args[0]
    assert msg.answer.await_args.kwargs.get("reply_markup") is None


async def test_a_bootstrap_admin_with_no_row_is_served(session):
    # id is None: identity.apply_bootstrap hands out a transient principal, and
    # nothing here writes, so it must not be refused.
    _seed(session)
    msg = _msg()
    await cmd_cohort(msg, principal=User(last_name="Boot", role=Role.ADMIN),
                     session=session, command=_args("2024"))
    msg.answer_document.assert_awaited_once()


def _cb(data, text="Which cohort?"):
    from aiogram.types import Message
    message = AsyncMock(spec=Message)
    message.text = text
    return SimpleNamespace(data=data, message=message, answer=AsyncMock())


async def test_tapping_a_cohort_replaces_the_text_and_sends_the_file(session):
    _seed(session)
    cb = _cb(f"{PICK_PREFIX}2024")
    await cb_pick(cb, principal=User(last_name="A", role=Role.ADMIN),
                  session=session)
    assert "Ivan Ivanov" in cb.message.edit_text.await_args.args[0]
    assert cb.message.edit_text.await_args.kwargs["reply_markup"] is not None
    assert cb.message.answer_document.await_args.args[0].filename == \
        "cohort-2024.csv"
    cb.answer.assert_awaited()


async def test_tapping_the_open_cohort_again_only_resends_the_file(session):
    # Telegram rejects an edit that changes nothing; the file is the point of
    # the tap, so it still goes out.
    _seed(session)
    cb = _cb(f"{PICK_PREFIX}2024")
    await cb_pick(cb, principal=User(last_name="A", role=Role.ADMIN),
                  session=session)
    same = _cb(f"{PICK_PREFIX}2024", text=cb.message.edit_text.await_args.args[0])
    await cb_pick(same, principal=User(last_name="A", role=Role.ADMIN),
                  session=session)
    same.message.edit_text.assert_not_awaited()
    same.message.answer_document.assert_awaited_once()


async def test_a_student_tapping_a_stale_button_is_refused(session):
    _seed(session)
    cb = _cb(f"{PICK_PREFIX}2024")
    await cb_pick(cb, principal=User(last_name="S", role=Role.STUDENT),
                  session=session)
    cb.message.answer_document.assert_not_awaited()
    assert cb.answer.await_args.kwargs.get("show_alert") is True
```

Also invert the stale expectation in `tests/test_directory_handlers.py:68`:

```python
async def test_cohort_list_omits_a_departed_mate_for_an_admin_too(session):
    # A roster listing is about who is here now; an admin finds a departed
    # person by name instead.
    _seed_departed(session)
    msg = SimpleNamespace(answer=AsyncMock(), answer_document=AsyncMock())
    await cmd_cohort(msg, principal=_viewer(Role.ADMIN), session=session,
                     command=SimpleNamespace(args=None))
    assert "Expelled" not in msg.answer.await_args.args[0]
```

and fix that file's import (`cmd_cohort` now lives in `cohort`), plus pass
`command=SimpleNamespace(args=None)` in the two other `cmd_cohort` calls there
(`:53`, `:60`).

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_directory_cohort.py tests/test_directory_handlers.py -v`
Expected: FAIL — `ModuleNotFoundError: ... directory.cohort`.

- [ ] **Step 3: Implement**

Create `src/jbcub_bot/features/directory/cohort.py`:

```python
"""`/cohort`: your own cohort, or -- for staff -- any of them plus a CSV.

Staff have no `primary_cohort` of their own (a Rights row carries none), so
they pick one; the pick is a button per cohort, and the same name works as an
argument. The CSV is for matching these people in another system, so it goes
out with the list rather than behind a second tap.

Only current people are listed, for every role. A departed person is found by
name in the search, where `include_departed` still applies.
"""

from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from jbcub_bot.core.commands import CommandRegistrar
from jbcub_bot.core.models import User
from jbcub_bot.features.directory import export
from jbcub_bot.features.directory.render import render_cohort_list
from jbcub_bot.features.directory.screens import EXPIRED
from jbcub_bot.features.directory.search import list_cohort, list_cohort_names
from jbcub_bot.features.directory.visibility import is_staff

router = Router(name="directory.cohort")
cmd = CommandRegistrar(router)

PICK_PREFIX = "dir:cohort:"

_NO_COHORT = "No cohort on file."
_PICK = "Which cohort?"
_NO_COHORTS = "No cohorts on file yet — run /sync."
_STAFF_ONLY = "Staff only."
_BUTTONS_PER_ROW = 2


def picker_keyboard(names: list[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=name, callback_data=f"{PICK_PREFIX}{name}")
               for name in names]
    rows = [buttons[i:i + _BUTTONS_PER_ROW]
            for i in range(0, len(buttons), _BUTTONS_PER_ROW)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_list(viewer: User, cohort: str, people: list[User]) -> str:
    return f"{cohort} — {len(people)} people:\n{render_cohort_list(viewer, people)}"


def _match(names: list[str], wanted: str) -> str | None:
    """The cohort a typed name means. Case-insensitive: '2024b' is hand-typed."""
    folded = wanted.strip().casefold()
    return next((name for name in names if name.casefold() == folded), None)


@cmd.command("cohort", "List the people in your cohort.")
async def cmd_cohort(message: Message, principal: User, session,
                     command: CommandObject | None = None):
    """A student sees their own cohort; staff choose one and get a CSV.

    `command` is optional because /as propagates a message without the
    Dispatcher's outer middlewares -- a required parameter would make
    `/as <ref> /cohort` a TypeError.
    """
    if not is_staff(principal):
        if not principal.primary_cohort:
            await message.answer(_NO_COHORT)
            return
        people = list_cohort(session, principal.primary_cohort)
        await message.answer("Your cohort:\n" + render_cohort_list(principal, people))
        return

    names = list_cohort_names(session)
    if not names:
        await message.answer(_NO_COHORTS)
        return
    wanted = (command.args if command else None) or ""
    if not wanted.strip():
        await message.answer(_PICK, reply_markup=picker_keyboard(names))
        return
    cohort = _match(names, wanted)
    if cohort is None:
        await message.answer(f"No cohort named {wanted.strip()!r}. {_PICK}",
                             reply_markup=picker_keyboard(names))
        return
    people = list_cohort(session, cohort)
    await message.answer(render_list(principal, cohort, people))
    await _send_csv(message, principal, cohort, people)


async def _send_csv(message: Message, viewer: User, cohort: str,
                    people: list[User]) -> None:
    """The list as a file, when there is anything to put in it.

    A separate message rather than a caption: a caption is capped at 1024
    characters and a cohort list is not.
    """
    data = export.cohort_csv(viewer, people)
    if not data:
        return
    await message.answer_document(
        BufferedInputFile(data, filename=export.csv_filename(cohort))
    )


@router.callback_query(F.data.startswith(PICK_PREFIX))
async def cb_pick(cb: CallbackQuery, principal: User, session):
    """Redraw this message as the chosen cohort and send its CSV.

    Not `require_linked`: nothing here writes, and a bootstrap admin's
    principal has `id is None` -- that guard would refuse exactly the admin who
    has no row yet. Staffness is what matters, and it is re-checked because a
    keyboard outlives the role that drew it.
    """
    if principal is None or not is_staff(principal):
        await cb.answer(_STAFF_ONLY, show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    cohort = cb.data[len(PICK_PREFIX):]
    names = list_cohort_names(session)
    if cohort not in names:
        await cb.answer(EXPIRED, show_alert=True)
        return
    people = list_cohort(session, cohort)
    screen = render_list(principal, cohort, people)
    # Tapping the cohort already on screen would send an edit that changes
    # nothing, which Telegram rejects instead of ignoring. The file is the
    # point of the tap, so it still goes out.
    if cb.message.text != screen:
        await cb.message.edit_text(screen, reply_markup=picker_keyboard(names))
    await _send_csv(cb.message, principal, cohort, people)
    await cb.answer()
```

Delete `cmd_cohort` from `handlers.py` (`:83-90`) and drop `render_cohort_list`
from its `render` import list and `list_cohort` from its `search` import
(`rank_users` stays).

Wire it up in `src/jbcub_bot/features/directory/__init__.py`:

```python
from jbcub_bot.features.directory import cohort, edit, grades, privacy
from jbcub_bot.features.directory.handlers import cmd, name_search_intent, router

router.include_router(privacy.router)
router.include_router(edit.router)
router.include_router(grades.router)
router.include_router(cohort.router)

manifest = Manifest(
    name="directory",
    commands=cmd.specs + privacy.cmd.specs + edit.cmd.specs + cohort.cmd.specs,
    ...
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest`
Expected: PASS — including `test_manifest_exposes_contract`, which asserts
`cohort` is still among the manifest's commands.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/cohort.py \
        src/jbcub_bot/features/directory/handlers.py \
        src/jbcub_bot/features/directory/__init__.py \
        tests/test_directory_cohort.py tests/test_directory_handlers.py
git commit -m "feat: staff pick a cohort and get it as a CSV"
```

---

### Task 5: record the two new conventions

**Files:**
- Modify: `AGENTS.md:29` (the `departed_at` bullet), `AGENTS.md:33-40` (the FIELDS bullet)

- [ ] **Step 1: Update the `departed_at` bullet**

Replace its last sentence — "Row-level hiding is separate and opt-in: `search.rank_users`/`list_cohort` take `include_departed`, which callers pass from `handlers.is_admin`." — with:

```markdown
Row-level hiding is separate and opt-in: `search.rank_users`/`list_cohort` take
`include_departed`, and the name search is the only caller that passes it (from
`handlers.is_admin`). `/cohort` and its CSV list current people only, for every
role — a roster listing states who is here now, and `search.list_cohort_names`
skips a cohort whose last member left.
```

- [ ] **Step 2: Update the FIELDS bullet**

In "name, label, category (`ALWAYS` / `CONFIGURABLE` / `ADMIN_ONLY`)", make the
category list `ALWAYS` / `CONFIGURABLE` / `STAFF` / `ADMIN_ONLY`, and add to the
end of the bullet:

```markdown
`STAFF` is for a field admins *and teachers* read but its owner never sees --
`matriculation` and `telegram_id`, the keys another system matches people on.
`features/directory/export.py` derives a cohort CSV's columns from whatever
`visible_fields` returned for the people in hand (headers are field names, not
labels, and values come back with `merged=False`), so a field hidden on the
profile screen cannot appear in the file.
```

- [ ] **Step 3: Verify nothing else drifted**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs: note the STAFF category and the cohort CSV"
```
