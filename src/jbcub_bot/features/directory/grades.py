"""Resolve and store Gradebook rows, and serve the staff-only grades screen."""

from collections import Counter
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete, select

from jbcub_bot.core import gradebook, identity
from jbcub_bot.core.models import Grade, User
from jbcub_bot.features.directory.render import (
    GRADES_BACK_CALLBACK,
    GRADES_CALLBACK,
    profile_entities,
    profile_keyboard,
    render_profile,
)
from jbcub_bot.features.directory.screens import EXPIRED
from jbcub_bot.features.directory.visibility import is_staff


@dataclass(frozen=True)
class CountedName:
    name: str
    count: int


@dataclass
class GradesSyncReport:
    source_people: int = 0
    matched_people: int = 0
    cells: int = 0
    no_roster_match: list[str] = field(default_factory=list)
    ambiguous_roster_match: list[CountedName] = field(default_factory=list)
    missing_gradebook_rows: list[str] = field(default_factory=list)
    duplicate_rows: list[CountedName] = field(default_factory=list)
    ignored_columns: list[gradebook.IgnoredColumn] = field(default_factory=list)


def sync_cohort(
    session,
    cohort: str,
    rows: list[list[str]],
    mapping: dict,
    fold,
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

    candidates = session.scalars(
        select(User).where(User.primary_cohort == cohort)
    ).all()
    by_name: dict[tuple[str, str], list[User]] = {}
    for user in candidates:
        by_name.setdefault((fold(user.last_name), fold(user.first_name)), []).append(user)

    source_keys = set(names)
    report.missing_gradebook_rows = sorted(
        f"{user.last_name} {user.first_name}".strip()
        for user in candidates
        if user.departed_at is None
        and user.last_name
        and user.first_name
        and (fold(user.last_name), fold(user.first_name)) not in source_keys
    )

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
    return list(session.scalars(
        select(Grade).where(Grade.user_id == user_id).order_by(Grade.position)
    ).all())


def group_by_term(rows: list[Grade]) -> dict[str, list[Grade]]:
    groups: dict[str, list[Grade]] = {}
    for grade in rows:
        groups.setdefault(grade.term, []).append(grade)
    return groups


def has_grades(session, user_id: int) -> bool:
    return session.scalar(
        select(Grade.id).where(Grade.user_id == user_id).limit(1)
    ) is not None


def _render_body(rows: list[Grade]) -> str:
    lines = []
    last_category = None
    for grade in rows:
        if grade.category:
            if grade.category != last_category:
                lines.append(grade.category)
                last_category = grade.category
        else:
            last_category = None
        lines.append(f"• {grade.label}: {grade.value}")
    return "\n".join(lines)


def render_screen(term: str, rows: list[Grade]) -> str:
    text = f"{term}\n\n{_render_body(rows)}"
    if len(text) > _TEXT_LIMIT:
        text = text[: _TEXT_LIMIT - len(_TRUNCATE_MARK)] + _TRUNCATE_MARK
    return text


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


router = Router(name="directory.grades")


@router.callback_query(F.data.startswith(f"{GRADES_CALLBACK}:"))
async def cb_grades(cb: CallbackQuery, principal: User, session):
    if principal is None or not is_staff(principal):
        await cb.answer("Staff only.", show_alert=True)
        return
    _, _, matriculation, index_text = cb.data.split(":")
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
        )
    await cb.answer()


@router.callback_query(F.data.startswith(f"{GRADES_BACK_CALLBACK}:"))
async def cb_grades_back(cb: CallbackQuery, principal: User, session):
    if principal is None or not is_staff(principal):
        await cb.answer("Staff only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
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
