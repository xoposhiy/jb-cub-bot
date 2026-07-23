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

from aiogram.filters import CommandObject

from sdt_bot.core import sheets
from sdt_bot.core.sheets_client import fetch_rows
from sdt_bot.core.tokens import verify_link_token

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


@router.message(Command("start"))
async def cmd_start(message: Message, principal: User, session,
                    command: CommandObject):
    settings = get_settings()
    payload = command.args
    if payload:  # one-time link binding
        user = verify_link_token(session, payload, settings.link_secret,
                                 settings.link_ttl_seconds)
        if user is None:
            await message.answer("This link is invalid or expired.")
            return
        identity.bind_by_token(session, message.from_user.id,
                               message.from_user.username, user)
        await message.answer(f"Linked as {user.name}.")
        return
    if principal is not None:
        await message.answer(f"Welcome back, {principal.name}.")
    else:
        await message.answer(
            "I couldn't recognize you. Ask a program admin for a one-time link."
        )


@router.message(Command("sync"))
async def cmd_sync(message: Message, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await message.answer("Admins only.")
        return
    settings = get_settings()
    sa = settings.google_service_account_file

    # --- Parse phase: fetch + normalize everything; write nothing yet. ---
    try:
        index_rows = fetch_rows(settings.rights_sheet_id, sa,
                                f"{settings.cohorts_tab}!A:Z")
        cohorts = sheets.parse_cohort_index(index_rows)
    except sheets.MappingError as exc:
        await message.answer(f"Sync aborted (Cohorts tab): {exc}")
        return

    parsed_cohorts = []  # (cohort_name, records)
    for entry in cohorts:
        sheet_id = sheets.extract_sheet_id(entry["link"])
        try:
            rows = fetch_rows(sheet_id, sa)
            mapping = sheets.load_mapping(f"{settings.mapping_dir}/{entry['mapping']}")
            records = sheets.normalize_rows(rows, mapping)
        except (sheets.MappingError, FileNotFoundError) as exc:
            await message.answer(f"Sync aborted (cohort {entry['cohort']}): {exc}")
            return
        for record in records:
            record["primary_cohort"] = entry["cohort"]
        parsed_cohorts.append((entry["cohort"], records))

    try:
        rights_rows = fetch_rows(settings.rights_sheet_id, sa,
                                 f"{settings.rights_tab}!A:Z")
        rights_mapping = sheets.load_mapping(
            f"{settings.mapping_dir}/{settings.rights_mapping}"
        )
        rights_records = sheets.normalize_rows(rights_rows, rights_mapping)
    except (sheets.MappingError, FileNotFoundError) as exc:
        await message.answer(f"Sync aborted (Rights tab): {exc}")
        return

    # --- Write phase: everything parsed OK, now upsert + reconcile. ---
    lines = ["Sync done."]
    for cohort_name, records in parsed_cohorts:
        sheets.upsert_users(session, records)
        rep = sheets.reconcile(session, records)
        lines.append(f"{cohort_name}: {len(records)} rows, drift={rep.drift or '-'}, "
                     f"unmatched={rep.unmatched or '-'}, dup={rep.duplicates or '-'}")
    sheets.upsert_users(session, rights_records)
    rep = sheets.reconcile(session, rights_records)
    lines.append(f"rights: {len(rights_records)} rows, drift={rep.drift or '-'}, "
                 f"unmatched={rep.unmatched or '-'}, dup={rep.duplicates or '-'}")
    await message.answer("\n".join(lines))
