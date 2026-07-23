from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from sdt_bot.core import identity
from sdt_bot.core.config import get_settings
from sdt_bot.core.intents import Intent
from sdt_bot.core.models import Role, User
from sdt_bot.core.tokens import issue_link_token
from sdt_bot.features.directory.render import admin_keyboard, render_profile
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
    kb = admin_keyboard(principal) if principal.role is Role.ADMIN else None
    await message.answer(render_profile(principal, principal), reply_markup=kb)


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
        target = results[0]
        kb = admin_keyboard(target) if principal.role is Role.ADMIN else None
        await message.answer(render_profile(principal, target), reply_markup=kb)
    else:
        lines = [f"- {u.name}" for u in results[:20]]
        await message.answer("Several people match:\n" + "\n".join(lines))


name_search_intent = Intent(
    name="directory.search", pattern=r".+", handler=name_search
)


@router.callback_query(F.data.startswith("dir:link:"))
async def cb_issue_link(cb: CallbackQuery, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await cb.answer("Admins only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
    try:
        token = issue_link_token(session, matriculation, get_settings().link_secret)
    except ValueError:
        await cb.answer("Not found.", show_alert=True)
        return
    bot_user = await cb.bot.me()
    await cb.message.answer(
        f"One-time link:\nhttps://t.me/{bot_user.username}?start={token}"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("dir:reset:"))
async def cb_reset(cb: CallbackQuery, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await cb.answer("Admins only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
    ok = identity.reset_binding(session, matriculation)
    await cb.answer("Reset done." if ok else "Not found.", show_alert=True)
