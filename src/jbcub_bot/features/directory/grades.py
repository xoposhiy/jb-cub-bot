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


@dataclass
class GradesSyncReport:
    matched: int = 0
    cells: int = 0
    unmatched: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)
    ignored_columns: int = 0


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
    report = GradesSyncReport(ignored_columns=parsed.ignored_columns)

    names = [(fold(row.last_name), fold(row.first_name)) for row in parsed.rows]
    duplicate_keys = {key for key, count in Counter(names).items() if count > 1}

    candidates = session.scalars(
        select(User).where(User.primary_cohort == cohort)
    ).all()
    by_name: dict[tuple[str, str], list[User]] = {}
    for user in candidates:
        by_name.setdefault((fold(user.last_name), fold(user.first_name)), []).append(user)

    session.execute(delete(Grade).where(Grade.cohort == cohort))

    columns = {column.index: column for column in parsed.columns}
    for row, key in zip(parsed.rows, names):
        name = f"{row.last_name} {row.first_name}"
        if key in duplicate_keys:
            report.duplicates.append(name)
            continue
        matches = by_name.get(key)
        if not matches or len(matches) > 1:
            report.unmatched.append(name)
            continue
        user = matches[0]
        report.matched += 1
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
