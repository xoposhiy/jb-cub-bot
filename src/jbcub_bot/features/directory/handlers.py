import asyncio
import json

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from jbcub_bot.core import identity
from jbcub_bot.core.commands import CommandRegistrar
from jbcub_bot.core.config import get_settings
from jbcub_bot.core.intents import Intent
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.tokens import issue_link_token
from jbcub_bot.features.directory.render import (
    admin_keyboard,
    me_keyboard,
    render_cohort_list,
    render_profile,
)
from jbcub_bot.features.directory.search import list_cohort, rank_users

from aiogram.filters import CommandObject

from jbcub_bot.core import sheets
from jbcub_bot.core.sheets_client import build_credentials, fetch_rows
from jbcub_bot.core.tokens import verify_link_token

router = Router(name="directory")
cmd = CommandRegistrar(router)


# A Sheets read that never answers must not take the bot with it. googleapiclient
# already applies a 60s socket timeout, so this only has to be the outer bound.
SHEET_READ_TIMEOUT = 90.0


def set_status(session, user: User, text: str) -> None:
    user.status_line = text
    session.commit()


async def read_rows(sheet_id: str, credentials, range_: str = "A:Z") -> list[list[str]]:
    """`fetch_rows` off the event loop, with a deadline.

    fetch_rows is blocking: awaited inline it stalls every other update until
    Google answers. The thread keeps running after a timeout — it can't be
    cancelled — but the bot stays responsive and the failure gets reported
    instead of looking like a hang.
    """
    async with asyncio.timeout(SHEET_READ_TIMEOUT):
        return await asyncio.to_thread(fetch_rows, sheet_id, credentials, range_)


@cmd.command("me", "Show your own profile.")
async def cmd_me(message: Message, principal: User, session, impersonator=None):
    # `impersonator` is only in the handler context while /as is in flight; a
    # button press afterwards would arrive as the admin, so hide the screen.
    await message.answer(
        render_profile(principal, principal),
        reply_markup=me_keyboard(principal, interactive=impersonator is None),
    )


@cmd.command("cohort", "List the people in your cohort.")
async def cmd_cohort(message: Message, principal: User, session):
    if not principal.primary_cohort:
        await message.answer("No cohort on file.")
        return
    mates = list_cohort(session, principal.primary_cohort)
    await message.answer("Your cohort:\n" + render_cohort_list(principal, mates))


async def name_search(message: Message, principal: User, session):
    if principal is None:
        await message.answer("You are not linked yet. Contact an admin.")
        return
    query = (message.text or "").strip()
    results = [user for _, user in rank_users(session, query)]
    if not results:
        await message.answer("No one found.")
    elif len(results) == 1:
        target = results[0]
        kb = admin_keyboard(target) if principal.role is Role.ADMIN else None
        await message.answer(render_profile(principal, target), reply_markup=kb)
    else:
        lines = [f"- {u.full_name}" for u in results[:20]]
        await message.answer("Several people match:\n" + "\n".join(lines))


name_search_intent = Intent(
    name="directory.search",
    pattern=r".+",
    handler=name_search,
    description="just type a name — search people",
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
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Yes, reset",
                             callback_data=f"dir:reset_do:{matriculation}"),
        InlineKeyboardButton(text="Cancel", callback_data="dir:reset_cancel"),
    ]])
    await cb.message.answer(
        f"🧐 Reset telegram_id for {matriculation}? This unlinks their Telegram "
                f"account — they'll still have access if their telegram handle match the profile "
                f"or need a new one-time link to access the bot.\n\n👿 Use it in case of when user lost access to their Telegram account and created a new one"
                f"Probably you also need to change their Telegram handle in that case. Change it in the Google Sheet to actual value and run /sync.",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("dir:reset_do:"))
async def cb_reset_do(cb: CallbackQuery, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await cb.answer("Admins only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
    ok = identity.reset_binding(session, matriculation)
    await cb.message.edit_text("Reset done." if ok else "Not found.")
    await cb.answer()


@router.callback_query(F.data == "dir:reset_cancel")
async def cb_reset_cancel(cb: CallbackQuery, principal: User, session):
    await cb.message.edit_text("Reset cancelled.")
    await cb.answer()


@cmd.command("start", "Start / link your account.", public=True)
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
        await message.answer(f"Linked as {user.full_name}.")
        return
    if principal is not None:
        await message.answer(f"Welcome back, {principal.full_name}.")
    else:
        await message.answer(
            "I couldn't recognize you. Ask a program admin for a one-time link."
        )


@cmd.command("sync", "Re-sync roster from Google Sheets.", min_role=Role.ADMIN)
async def cmd_sync(message: Message, principal: User, session):
    settings = get_settings()
    try:
        sa = build_credentials(settings.google_service_account_file,
                               settings.google_service_account_json)
    except (ValueError, FileNotFoundError, IsADirectoryError,
             json.JSONDecodeError) as exc:
        await message.answer(f"Sync aborted (credentials): {exc}")
        return

    # --- Parse phase: fetch + normalize everything; write nothing yet. ---
    await message.answer("Sync started. Reading sheets…")
    try:
        index_rows = await read_rows(settings.rights_sheet_id, sa,
                                     f"{settings.cohorts_tab}!A:Z")
        cohorts = sheets.parse_cohort_index(index_rows)
    except sheets.MappingError as exc:
        await message.answer(f"Sync aborted (Cohorts tab): {exc}")
        return
    except Exception as exc:  # network / API / anything unforeseen
        raise RuntimeError("/sync failed reading the Cohorts tab") from exc

    parsed_cohorts = []  # (cohort_name, records)
    for entry in cohorts:
        await message.answer(f"Reading cohort {entry['cohort']}…")
        sheet_id = sheets.extract_sheet_id(entry["link"])
        try:
            rows = await read_rows(sheet_id, sa)
            mapping = sheets.load_mapping(f"{settings.mapping_dir}/{entry['mapping']}")
            records = sheets.normalize_rows(rows, mapping)
        except (sheets.MappingError, FileNotFoundError) as exc:
            await message.answer(f"Sync aborted (cohort {entry['cohort']}): {exc}")
            return
        except Exception as exc:
            raise RuntimeError(
                f"/sync failed reading cohort {entry['cohort']} (sheet {sheet_id})"
            ) from exc
        for record in records:
            record["primary_cohort"] = entry["cohort"]
        parsed_cohorts.append((entry["cohort"], records))
        await message.answer(f"Cohort {entry['cohort']}: {len(records)} rows read.")

    await message.answer("Reading Rights…")
    try:
        rights_rows = await read_rows(settings.rights_sheet_id, sa,
                                      f"{settings.rights_tab}!A:Z")
        rights_mapping = sheets.load_mapping(
            f"{settings.mapping_dir}/{settings.rights_mapping}"
        )
        rights_records = sheets.normalize_rows(rights_rows, rights_mapping)
    except (sheets.MappingError, FileNotFoundError) as exc:
        await message.answer(f"Sync aborted (Rights tab): {exc}")
        return
    except Exception as exc:
        raise RuntimeError("/sync failed reading the Rights tab") from exc

    for record in rights_records:
        role_value = record.get("role")
        if role_value:
            try:
                Role(role_value)
            except ValueError:
                await message.answer(
                    f"Sync aborted (Rights tab): invalid role {role_value!r}"
                )
                return
    await message.answer(f"Rights: {len(rights_records)} rows read.")

    # --- Write phase: everything parsed OK, now upsert + reconcile. ---
    await message.answer("All sheets read. Writing to database…")
    try:
        for cohort_name, records in parsed_cohorts:
            sheets.upsert_users(session, records)
            rep = sheets.reconcile(session, records)
            await message.answer(
                f"{cohort_name}: {len(records)} rows, drift={rep.drift or '-'}, "
                f"unmatched={rep.unmatched or '-'}, dup={rep.duplicates or '-'}")
        # Rights rows have no matriculation — key on the Telegram handle so
        # admins/teachers get matched (or created) as searchable rows.
        sheets.upsert_users(session, rights_records, key="handle_sheet")
        rep = sheets.reconcile(session, rights_records, key="handle_sheet")
        await message.answer(
            f"rights: {len(rights_records)} rows, drift={rep.drift or '-'}, "
            f"unmatched={rep.unmatched or '-'}, dup={rep.duplicates or '-'}")
        session.commit()
    except Exception as exc:
        session.rollback()  # the roster keeps its last good state
        raise RuntimeError("/sync failed in the write phase") from exc
    await message.answer("Sync done.")
