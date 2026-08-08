"""`/cohort`: your own cohort, or -- for staff -- any of them plus a CSV.

Staff have no `primary_cohort` of their own (a Rights row carries none), so
they pick one; the pick is a button per cohort, and the same name works as an
argument. The CSV is for matching these people in another system, so it goes
out with the list rather than behind a second tap.

Only current people are listed, for every role. A departed person is found by
name in the search, where `include_departed` still applies.
"""

from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from jbcub_bot.core.models import User
from jbcub_bot.features.directory import export
from jbcub_bot.features.directory.render import render_cohort_list
from jbcub_bot.features.directory.screens import EXPIRED
from jbcub_bot.features.directory.search import list_cohort, list_cohort_names
from jbcub_bot.features.directory.visibility import is_staff

# The button key, without the separator that divides it from the cohort name.
PICK_CALLBACK = "dir:cohort"

_NO_COHORT = "No cohort on file."
_PICK = "Which cohort?"
_NO_COHORTS = "No cohorts on file yet — run /sync."
_BUTTONS_PER_ROW = 2

# Telegram caps callback_data at 64 bytes and aiogram never checks client-side,
# so a button built from an over-long name would make the whole send fail --
# taking the picker, the unknown-name redraw and every cb_pick edit down with it.
_MAX_CALLBACK_BYTES = 64


def _fits_callback(name: str) -> bool:
    """Whether `name` can ride a `dir:cohort:` button without hitting the cap."""
    return len(f"{PICK_CALLBACK}:{name}".encode()) <= _MAX_CALLBACK_BYTES


def _overflow_note(names: list[str]) -> str:
    """Told in the picker's text, since these names get no button at all."""
    long_names = [name for name in names if not _fits_callback(name)]
    if not long_names:
        return ""
    return ("\nToo long for a button — type /cohort <name> instead: "
            + ", ".join(long_names))


def picker_keyboard(names: list[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=name,
                                    callback_data=f"{PICK_CALLBACK}:{name}")
               for name in names if _fits_callback(name)]
    rows = [buttons[i:i + _BUTTONS_PER_ROW]
            for i in range(0, len(buttons), _BUTTONS_PER_ROW)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_list(viewer: User, cohort: str, people: list[User]) -> str:
    noun = "person" if len(people) == 1 else "people"
    return f"{cohort} — {len(people)} {noun}:\n{render_cohort_list(viewer, people)}"


def _match(names: list[str], wanted: str) -> str | None:
    """The cohort a typed name means. Case-insensitive: '2024b' is hand-typed."""
    folded = wanted.strip().casefold()
    return next((name for name in names if name.casefold() == folded), None)


async def cmd_cohort(message: Message, principal: User, session, arg: str):
    """A student sees their own cohort; staff choose one and get a CSV.

    `is_staff` here is not a guard -- both roles may run /cohort, and this is
    the branch that decides which of the two screens they get.
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
    wanted = arg
    if not wanted.strip():
        await message.answer(_PICK + _overflow_note(names),
                             reply_markup=picker_keyboard(names))
        return
    cohort = _match(names, wanted)
    if cohort is None:
        await message.answer(
            f"No cohort named {wanted.strip()!r}. {_PICK}{_overflow_note(names)}",
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


async def cb_pick(cb: CallbackQuery, principal: User, session, arg: str):
    """Redraw this message as the chosen cohort and send its CSV.

    No "no saved row" check, deliberately: nothing here writes, and a bootstrap
    admin's principal has `id is None` -- refusing it would turn away exactly
    the admin who has no row yet. `role=Role.TEACHER` on the registration is
    what re-checks staffness, because a keyboard outlives the role that drew it.
    """
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    cohort = arg
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
