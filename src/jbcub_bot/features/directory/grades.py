"""Resolve and store Gradebook rows, and serve the staff-only grades screen."""

from collections import Counter
from dataclasses import dataclass, field

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
)
from sqlalchemy import delete, select

from jbcub_bot.core import identity
from jbcub_bot.core.models import Grade, User
from jbcub_bot.features.directory import gradebook
from jbcub_bot.features.directory.sheets import cohort_start
from jbcub_bot.features.directory.visibility import cohorts_of
from jbcub_bot.features.directory.render import (
    GRADES_BACK_CALLBACK,
    GRADES_CALLBACK,
    profile_entities,
    profile_keyboard,
    render_profile,
)
from jbcub_bot.features.directory.screens import EXPIRED


@dataclass(frozen=True)
class CountedName:
    name: str
    count: int


@dataclass
class GradesSyncReport:
    source_people: int = 0
    matched_people: int = 0
    current_roster_people: int = 0
    current_roster_found: int = 0
    cells: int = 0
    no_roster_match: list[str] = field(default_factory=list)
    ambiguous_roster_match: list[CountedName] = field(default_factory=list)
    missing_gradebook_rows: list[str] = field(default_factory=list)
    unmatchable_roster_rows: list[str] = field(default_factory=list)
    duplicate_rows: list[CountedName] = field(default_factory=list)
    ignored_columns: list[gradebook.IgnoredColumn] = field(default_factory=list)


def sync_cohort(
    session,
    cohort: str,
    rows: list[list[str]],
    mapping: dict,
    fold,
    current_roster_records: list[dict] | None = None,
) -> GradesSyncReport:
    """Replace one cohort's grades after resolving exact folded names."""
    parsed = gradebook.parse_gradebook(
        rows, mapping["last_name"], mapping["first_name"]
    )
    report = GradesSyncReport(
        source_people=len(parsed.rows),
        ignored_columns=parsed.ignored_columns,
    )

    names = [(fold(row.last_name), fold(row.first_name)) for row in parsed.rows]
    counts = Counter(names)
    display_names = {
        key: f"{row.last_name} {row.first_name}".strip()
        for row, key in zip(parsed.rows, names)
    }
    report.duplicate_rows = [
        CountedName(name=display_names[key], count=count)
        for key, count in counts.items()
        if count > 1
    ]

    # A master student is still matched against the bachelor Gradebook they
    # came from.
    candidates = [user for user in session.scalars(select(User)).all()
                  if cohort in cohorts_of(user)]
    by_name: dict[tuple[str, str], list[User]] = {}
    for user in candidates:
        by_name.setdefault((fold(user.last_name), fold(user.first_name)), []).append(user)

    source_keys = set(names)
    if current_roster_records is None:
        current_roster_records = [
            {
                "matriculation": user.matriculation,
                "last_name": user.last_name,
                "first_name": user.first_name,
            }
            for user in candidates
            if user.departed_at is None
        ]
    report.current_roster_people = len(current_roster_records)
    for record in current_roster_records:
        matriculation = str(record.get("matriculation") or "").strip()
        last_name = str(record.get("last_name") or "").strip()
        first_name = str(record.get("first_name") or "").strip()
        display_name = f"{last_name} {first_name}".strip() or matriculation
        missing_fields = []
        if not matriculation:
            missing_fields.append("matriculation number")
        if not last_name:
            missing_fields.append("last name")
        if not first_name:
            missing_fields.append("first name")
        if missing_fields:
            report.unmatchable_roster_rows.append(
                f"{display_name} — missing {', '.join(missing_fields)}"
            )
        if last_name and first_name:
            key = (fold(last_name), fold(first_name))
            if key in source_keys:
                report.current_roster_found += 1
            else:
                report.missing_gradebook_rows.append(display_name)

    session.execute(delete(Grade).where(Grade.cohort == cohort))

    columns = {column.index: column for column in parsed.columns}
    for row, key in zip(parsed.rows, names):
        if counts[key] > 1:
            continue
        matches = by_name.get(key, [])
        name = display_names[key]
        if not matches:
            report.no_roster_match.append(name)
            continue
        if len(matches) > 1:
            report.ambiguous_roster_match.append(
                CountedName(name=name, count=len(matches))
            )
            continue
        user = matches[0]
        report.matched_people += 1
        for index, value in row.cells.items():
            column = columns[index]
            session.add(Grade(
                user_id=user.id,
                cohort=cohort,
                term=column.term,
                category=column.category,
                label=column.label,
                value=value,
                position=index,
            ))
            report.cells += 1
    report.no_roster_match.sort()
    report.ambiguous_roster_match.sort(key=lambda item: item.name)
    report.missing_gradebook_rows.sort()
    report.unmatchable_roster_rows.sort()
    report.duplicate_rows.sort(key=lambda item: item.name)
    return report


_TERM_BUTTONS_PER_ROW = 3
_TEXT_LIMIT = 4096
_TRUNCATE_MARK = "\n… (truncated)"
# The screen names the open semester in its first line, but the keyboard is the
# only place a reader compares it against the others -- so it says which one is
# already on screen, and a tap that changes nothing looks deliberate.
_ACTIVE_TERM_MARK = "📍"


def load_grades(session, user_id: int) -> list[Grade]:
    """A person's grades in screen order.

    `position` is only an order within one Gradebook, so a person with grades
    from two cohorts gets the older cohort's first.
    """
    rows = session.scalars(select(Grade).where(Grade.user_id == user_id)).all()
    return sorted(rows, key=lambda grade: (cohort_start(grade.cohort),
                                           grade.cohort, grade.position))


def group_by_term(rows: list[Grade]) -> dict[str, list[Grade]]:
    groups: dict[str, list[Grade]] = {}
    for grade in rows:
        groups.setdefault(grade.term, []).append(grade)
    return groups


def has_grades(session, user_id: int) -> bool:
    return session.scalar(
        select(Grade.id).where(Grade.user_id == user_id).limit(1)
    ) is not None


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _screen_lines(term: str, rows: list[Grade]) -> list[tuple[str, bool]]:
    """Every line of the screen, each marked bold or not.

    The term and each category name are the section headers; sharing this
    list between `render_screen` and `grade_entities` keeps the bold spans
    from ever drifting out of sync with the text they mark.
    """
    lines: list[tuple[str, bool]] = [(term, True), ("", False)]
    last_category = None
    for grade in rows:
        if grade.category:
            if grade.category != last_category:
                lines.append((grade.category, True))
                last_category = grade.category
        else:
            last_category = None
        lines.append((f"• {grade.label}: {grade.value}", False))
    return lines


def render_screen(term: str, rows: list[Grade]) -> str:
    text = "\n".join(line for line, _bold in _screen_lines(term, rows))
    if len(text) > _TEXT_LIMIT:
        text = text[: _TEXT_LIMIT - len(_TRUNCATE_MARK)] + _TRUNCATE_MARK
    return text


def grade_entities(term: str, rows: list[Grade], text: str) -> list[MessageEntity]:
    """Bold the term and each category, so a long screen reads in sections
    instead of one dense block of bullets.

    Takes the already-rendered `text` so that if it was truncated, no entity
    ever points past the end of it.
    """
    limit = _utf16_len(text)
    entities = []
    offset = 0
    for line, bold in _screen_lines(term, rows):
        length = _utf16_len(line)
        if bold and length and offset + length <= limit:
            entities.append(
                MessageEntity(type="bold", offset=offset, length=length)
            )
        offset += length + 1  # the joining "\n"
    return entities


def semester_keyboard(
    matriculation: str, terms: list[str], active: str | None = None
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{_ACTIVE_TERM_MARK} {term}" if term == active else term,
            callback_data=f"{GRADES_CALLBACK}:{matriculation}:{index}",
        )
        for index, term in enumerate(terms)
    ]
    rows = [
        buttons[index : index + _TERM_BUTTONS_PER_ROW]
        for index in range(0, len(buttons), _TERM_BUTTONS_PER_ROW)
    ]
    rows.append([InlineKeyboardButton(
        text="⬅️ Back",
        callback_data=f"{GRADES_BACK_CALLBACK}:{matriculation}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def cb_grades(cb: CallbackQuery, principal: User, session, arg: str):
    # The one button carrying two values, so it splits its own payload. A
    # matriculation containing ":" would break it -- as it already did when the
    # whole of `cb.data` was split here, so nothing is newly fragile.
    matriculation, index_text = arg.split(":")
    target = identity.find_by_matriculation(session, matriculation)
    if target is None:
        await cb.answer("Not found.", show_alert=True)
        return
    groups = group_by_term(load_grades(session, target.id))
    terms = list(groups)
    try:
        term = terms[int(index_text)]
    except (ValueError, IndexError):
        await cb.answer(EXPIRED, show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    # Tapping the semester already open would send an edit that changes
    # nothing -- which Telegram rejects ("message is not modified") instead of
    # ignoring. The profile's Grades button opens the latest term, so that tap
    # is one button away. Comparing the text is enough: it is the whole screen
    # apart from the active mark, which moves with it.
    screen = render_screen(term, groups[term])
    if cb.message.text != screen:
        await cb.message.edit_text(
            screen,
            reply_markup=semester_keyboard(matriculation, terms, active=term),
            entities=grade_entities(term, groups[term], screen),
        )
    await cb.answer()


async def cb_grades_back(cb: CallbackQuery, principal: User, session,
                         arg: str):
    matriculation = arg
    target = identity.find_by_matriculation(session, matriculation)
    if target is None:
        await cb.answer("Not found.", show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    text = render_profile(principal, target)
    await cb.message.edit_text(
        text,
        reply_markup=profile_keyboard(
            principal, target, show_grades=has_grades(session, target.id)
        ),
        entities=profile_entities(principal, target, text),
    )
    await cb.answer()
