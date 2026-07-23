from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from sdt_bot.core.intents import Intent
from sdt_bot.core.models import User
from sdt_bot.features.directory.render import render_profile
from sdt_bot.features.directory.search import list_cohort, search_users

router = Router(name="directory")


def set_status(session, user: User, text: str) -> None:
    user.status_line = text
    session.commit()


@router.message(Command("me"))
async def cmd_me(message: Message, principal: User, session):
    if principal is None:
        await message.answer("You are not linked yet. Contact an admin.")
        return
    await message.answer(render_profile(principal, principal))


@router.message(Command("cohort"))
async def cmd_cohort(message: Message, principal: User, session):
    if principal is None or not principal.primary_cohort:
        await message.answer("No cohort on file.")
        return
    mates = list_cohort(session, principal.primary_cohort)
    lines = [f"- {m.name} (@{m.handle_observed or m.handle_sheet or '?'})"
             for m in mates]
    await message.answer("Your cohort:\n" + "\n".join(lines))


async def name_search(message: Message, principal: User, session):
    if principal is None:
        await message.answer("You are not linked yet. Contact an admin.")
        return
    query = (message.text or "").strip()
    results = search_users(session, query)
    if not results:
        await message.answer("No one found.")
    elif len(results) == 1:
        await message.answer(render_profile(principal, results[0]))
    else:
        lines = [f"- {u.name}" for u in results[:20]]
        await message.answer("Several people match:\n" + "\n".join(lines))


name_search_intent = Intent(
    name="directory.search", pattern=r".+", handler=name_search
)
